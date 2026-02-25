#!/usr/bin/env python3
"""
patch_echo_mini.py — Parche de firmware para Echo Mini (RKnano)
===============================================================

Qué hace este parche
--------------------
El Echo Mini maneja 5 temas visuales (A-E). Por defecto, la animación de
encendido y apagado es COMPARTIDA: sin importar el tema activo, siempre
muestra la misma imagen de boot/shutdown (la del Tema A).

Este parche modifica el firmware .IMG para que cada tema tenga su propia
animación de encendido y apagado, independiente de los demás.

Cambios que aplica
------------------
1.  Expande la tabla ROCK26 de recursos:
      Antes: 1 bloque compartido × 5 temas = 1 copia de las 67 imágenes de boot
      Después: 5 bloques independientes (una copia por tema, renombradas T_X_*)

2.  Expande la tabla de metadatos con las mismas entradas multiplicadas × 5.

3.  Parchea la instrucción CMP en el loader ARM Thumb2:
      CMP R0, #0x43  →  CMP R0, #0x00
    Esto desactiva el salto que forzaba siempre al bloque del Tema A.

4.  Parchea 4 instrucciones ADDW que calculan el offset del bloque de boot:
      Valores anteriores (307 × N)  →  Nuevos valores (374 × N)
    El nuevo tamaño de bloque = 307 recursos temáticos + 67 compartidos = 374.

5.  Actualiza el puntero fw_end y copia el header de 512 bytes al final del
    firmware para que el bootloader lo encuentre correctamente.

Notas de seguridad
------------------
- El Echo Mini incluye un campo CRC32 (trailer RKnano) pero NO lo verifica
  activamente al flashear. Por eso el parche NO recalcula el CRC32 — hacerlo
  con un tamaño de archivo incorrecto podría corromper el firmware.
- Siempre trabaja sobre una COPIA del .IMG original. Nunca parchea in-place
  sin hacer un backup primero.

Uso
---
    python patch_echo_mini.py HIFIEC20.IMG               # genera HIFIEC20_patched.IMG
    python patch_echo_mini.py HIFIEC20.IMG -o salida.IMG # destino personalizado
    python patch_echo_mini.py HIFIEC20.IMG --check       # solo verifica estado

Requisitos
----------
    Python 3.8+  (sin dependencias externas)
"""

import sys
import struct
import shutil
import argparse
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Constantes del firmware
# ─────────────────────────────────────────────────────────────────────────────

METADATA_ENTRY_SIZE = 108   # bytes por entrada en la tabla de metadatos
SHARED_COUNT        = 67    # recursos compartidos de boot (índices 0-66)


# ─────────────────────────────────────────────────────────────────────────────
# Parser mínimo del .IMG
# ─────────────────────────────────────────────────────────────────────────────

class FirmwarePatcher:
    """
    Carga un .IMG RKnano, detecta las estructuras internas y aplica el parche
    de boot personalizado por tema.
    """

    def __init__(self, img_path: Path):
        self.img_path = img_path
        self.data = bytearray(img_path.read_bytes())
        self._parse()

    # ── Parseo interno ────────────────────────────────────────────────────────

    def _parse(self):
        """Localiza Part5, tabla ROCK26 y tabla de metadatos."""
        info = struct.unpack('<IIII', self.data[0x14C:0x15C])
        self.part5_offset = info[0]
        self.part5_size   = info[1]
        part5 = self._part5()

        # Tabla ROCK26 (índice de recursos de imagen)
        rock26_off = part5.find(b'ROCK26IMAGERES')
        if rock26_off == -1:
            raise ValueError("No se encontró la tabla ROCK26 en el firmware.")

        self.rock26_off   = rock26_off
        self.rock26_start = rock26_off + 32
        self.rock26_count = struct.unpack('<I', part5[rock26_off + 16:rock26_off + 20])[0]

        # Tabla de metadatos (nombres, dimensiones, offsets)
        anchor = struct.unpack('<I', part5[self.rock26_start + 12:self.rock26_start + 16])[0]
        first_match = None
        for pos in range(0, len(part5) - METADATA_ENTRY_SIZE, 4):
            eoff = struct.unpack('<I', part5[pos + 20:pos + 24])[0]
            if eoff == anchor:
                nm = part5[pos + 32:pos + 96].split(b'\x00')[0].decode('ascii', errors='ignore')
                if nm.endswith('.BMP') and len(nm) >= 5:
                    first_match = pos
                    break

        if first_match is None:
            raise ValueError("No se encontró la tabla de metadatos en el firmware.")

        # Retroceder al inicio real de la tabla
        table_start = first_match
        while table_start >= METADATA_ENTRY_SIZE:
            tp = table_start - METADATA_ENTRY_SIZE
            tn = part5[tp + 32:tp + 96].split(b'\x00')[0].decode('ascii', errors='ignore')
            if tn and tn.endswith('.BMP') and len(tn) >= 3:
                table_start = tp
            else:
                break

        # Parsear entradas
        self.entries = []
        pos = table_start
        while pos + METADATA_ENTRY_SIZE <= len(part5):
            nm = part5[pos + 32:pos + 96].split(b'\x00')[0].decode('ascii', errors='ignore')
            if not nm or len(nm) < 3:
                break
            off = struct.unpack('<I', part5[pos + 20:pos + 24])[0]
            w   = struct.unpack('<I', part5[pos + 24:pos + 28])[0]
            h   = struct.unpack('<I', part5[pos + 28:pos + 32])[0]
            self.entries.append({'name': nm, 'offset': off, 'width': w, 'height': h, 'table_pos': pos})
            pos += METADATA_ENTRY_SIZE

        self.table_start = table_start

    def _part5(self) -> memoryview:
        return memoryview(self.data)[self.part5_offset:self.part5_offset + self.part5_size]

    # ── Helpers ARM Thumb2 ────────────────────────────────────────────────────

    @staticmethod
    def _decode_addw(hw1, hw2) -> int:
        """Decodifica ADDW Rd, Rn, #imm12 → devuelve el inmediato."""
        i    = (hw1 >> 10) & 1
        imm3 = (hw2 >> 12) & 0x7
        imm8 =  hw2 & 0xFF
        return (i << 11) | (imm3 << 8) | imm8

    @staticmethod
    def _encode_addw(imm12: int, rd: int = 0, rn: int = 0) -> bytes:
        """Codifica ADDW Rd, Rn, #imm12 → 4 bytes little-endian."""
        assert 0 <= imm12 < 4096, f"imm12 fuera de rango: {imm12}"
        i    = (imm12 >> 11) & 1
        imm3 = (imm12 >> 8) & 0x7
        imm8 =  imm12 & 0xFF
        hw1  = 0xF200 | (i << 10) | rn
        hw2  = (imm3 << 12) | (rd << 8) | imm8
        return struct.pack('<HH', hw1, hw2)

    # ── Detección del estado del parche ───────────────────────────────────────

    def detect(self) -> dict:
        """
        Escanea el firmware para encontrar CMP R0,#0x43 y los 4 ADDW siguientes.
        Devuelve un dict con toda la información de parcheo.
        """
        data = self.data
        cmp_offset = None

        for off in range(0x200, min(len(data), 0x400000), 2):
            val = struct.unpack_from('<H', data, off)[0]
            if val not in (0x2843, 0x2800):
                continue
            # Verificar que siguen 4 ADDW en los próximos ~80 bytes
            addw_offsets = []
            for scan in range(off + 2, off + 80, 2):
                if scan + 4 > len(data):
                    break
                hw1, hw2 = struct.unpack_from('<HH', data, scan)
                if (hw1 & 0xFBE0) == 0xF200 and (hw2 & 0x8F00) == 0x0000:
                    imm = self._decode_addw(hw1, hw2)
                    if imm > 100:
                        addw_offsets.append((scan, imm))
            if len(addw_offsets) >= 4:
                cmp_offset = off
                break

        if cmp_offset is None:
            raise ValueError("No se encontró la instrucción CMP de despacho de temas.")

        addw_list = []
        for scan in range(cmp_offset + 2, cmp_offset + 80, 2):
            if scan + 4 > len(data):
                break
            hw1, hw2 = struct.unpack_from('<HH', data, scan)
            if (hw1 & 0xFBE0) == 0xF200 and (hw2 & 0x8F00) == 0x0000:
                imm = self._decode_addw(hw1, hw2)
                if imm > 100:
                    addw_list.append((scan, imm))
                    if len(addw_list) == 4:
                        break

        if len(addw_list) < 4:
            raise ValueError(f"CMP encontrado pero solo {len(addw_list)} ADDW (se necesitan 4).")

        cmp_val     = struct.unpack_from('<H', data, cmp_offset)[0]
        is_patched  = (cmp_val == 0x2800)
        addw_values = [v for _, v in addw_list]

        if is_patched:
            old_block = addw_values[0] - SHARED_COUNT
        else:
            old_block = addw_values[0]

        return {
            'cmp_offset':    cmp_offset,
            'is_patched':    is_patched,
            'addw_list':     addw_list,
            'old_block_size': old_block,
            'new_block_size': old_block + SHARED_COUNT,
            'resource_count': self.rock26_count,
        }

    # ── Aplicar el parche ─────────────────────────────────────────────────────

    def apply_patch(self, progress_fn=None) -> str:
        """
        Aplica el parche completo. Devuelve un string resumen.
        Lanza ValueError si el firmware ya está parcheado.
        """
        info = self.detect()
        if info['is_patched']:
            return "El firmware ya tiene el parche aplicado (CMP R0,#0x00 detectado)."

        data      = self.data
        part5     = bytearray(self._part5())   # copia local para lectura
        SHARED    = SHARED_COUNT
        OLD_BLK   = info['old_block_size']
        NEW_BLK   = info['new_block_size']
        cmp_off   = info['cmp_offset']
        addw_list = info['addw_list']
        old_count = self.rock26_count

        # ── 1. Leer recursos compartidos (índices 0-66) ───────────────────────
        shared_r26  = []
        for i in range(SHARED):
            eo = self.rock26_start + i * 16
            shared_r26.append(bytes(part5[eo:eo + 16]))

        shared_meta = []
        for i in range(SHARED):
            tp = self.entries[i]['table_pos']
            shared_meta.append(bytes(part5[tp:tp + METADATA_ENTRY_SIZE]))

        # ── 2. Construir nuevas tablas: 5 bloques × NEW_BLK entradas ──────────
        new_r26  = []
        new_meta = []
        theme_letters = ['A', 'B', 'C', 'D', 'E']

        for t_idx, letter in enumerate(theme_letters):
            # 67 copias de recursos compartidos (renombradas T_X_* para temas B-E)
            for i in range(SHARED):
                new_r26.append(shared_r26[i])
                meta_raw = bytearray(shared_meta[i])
                if t_idx > 0:
                    orig_name = self.entries[i]['name']
                    new_name  = f"T_{letter}_{orig_name}"
                    nb = new_name.encode('ascii')[:63]
                    meta_raw[32:96] = nb + b'\x00' * (64 - len(nb))
                new_meta.append(bytes(meta_raw))

            # OLD_BLK recursos temáticos propios del tema
            old_start = SHARED + t_idx * OLD_BLK
            for i in range(OLD_BLK):
                src_idx = old_start + i
                if src_idx < old_count:
                    eo = self.rock26_start + src_idx * 16
                    new_r26.append(bytes(part5[eo:eo + 16]))
                else:
                    new_r26.append(shared_r26[0])
                if src_idx < len(self.entries):
                    tp = self.entries[src_idx]['table_pos']
                    new_meta.append(bytes(part5[tp:tp + METADATA_ENTRY_SIZE]))
                else:
                    new_meta.append(shared_meta[0])

            if progress_fn:
                progress_fn(int((t_idx + 1) * 14))   # 0-70 %

        new_count = len(new_r26)

        # ── 3. Escribir tabla ROCK26 expandida ───────────────────────────────
        r26_abs   = self.part5_offset + self.rock26_off
        count_abs = r26_abs + 16
        struct.pack_into('<I', data, count_abs, new_count)

        entries_abs = self.part5_offset + self.rock26_start
        for i, entry_raw in enumerate(new_r26):
            pos = entries_abs + i * 16
            if pos + 16 > len(data):
                data.extend(b'\x00' * (pos + 16 - len(data)))
            data[pos:pos + 16] = entry_raw

        # ── 4. Escribir tabla de metadatos expandida ─────────────────────────
        meta_abs = self.part5_offset + self.table_start
        for i, meta_raw in enumerate(new_meta):
            pos = meta_abs + i * METADATA_ENTRY_SIZE
            if pos + METADATA_ENTRY_SIZE > len(data):
                data.extend(b'\x00' * (pos + METADATA_ENTRY_SIZE - len(data)))
            data[pos:pos + METADATA_ENTRY_SIZE] = meta_raw

        if progress_fn:
            progress_fn(75)

        # ── 5. Actualizar tamaño de Part5 ────────────────────────────────────
        new_p5_end      = (meta_abs + len(new_meta) * METADATA_ENTRY_SIZE) - self.part5_offset
        self.part5_size = new_p5_end
        struct.pack_into('<I', data, 0x150, new_p5_end)

        # ── 6. Parchear CMP R0,#0x43 → CMP R0,#0x00 ─────────────────────────
        #
        #   Antes: 0x2843  → compara R0 con 0x43 (67 = SHARED_COUNT)
        #          Si R0 < 0x43 el firmware salta al bloque del Tema A siempre.
        #   Después: 0x2800 → compara con 0x00, por lo que nunca salta y cada
        #          tema usa su propio bloque de boot recién creado.
        #
        data[cmp_off:cmp_off + 2] = struct.pack('<H', 0x2800)

        # ── 7. Parchear los 4 ADDW de cálculo de offset ──────────────────────
        #
        #   Estos ADDW calculan el índice de inicio de cada bloque temático:
        #     ADDW R0, R0, #(OLD_BLK * N)   donde N = 1..4
        #   Con el nuevo tamaño de bloque deben usar NEW_BLK.
        #
        new_addw_vals = [NEW_BLK * (i + 1) for i in range(4)]
        for i, (foff, _old_val) in enumerate(addw_list):
            data[foff:foff + 4] = self._encode_addw(new_addw_vals[i], rd=0, rn=0)

        if progress_fn:
            progress_fn(85)

        # ── 8. Corregir puntero fw_end y copiar header ────────────────────────
        #
        #   NOTA: El Echo Mini no verifica activamente el CRC32 del trailer
        #   RKnano, por lo que se omite el recálculo del CRC para evitar
        #   posibles problemas derivados de un tamaño de archivo incorrecto.
        #
        self._fix_integrity()

        if progress_fn:
            progress_fn(100)

        return (
            f"✅ Parche aplicado correctamente\n"
            f"   CMP  : offset 0x{cmp_off:X}  (0x2843 → 0x2800)\n"
            f"   ADDW : {[v for _, v in addw_list]} → {new_addw_vals}\n"
            f"   Tabla: {old_count} → {new_count} entradas\n"
            f"   Bloque: {OLD_BLK} → {NEW_BLK} recursos/tema\n"
        )

    def _fix_integrity(self):
        """
        Actualiza fw_end y copia el header de 512 bytes al final del firmware.

        El bootloader del Echo Mini lee su propio header desde fw_end.
        Si no se actualiza este puntero tras expandir el firmware, el
        dispositivo no encontrará el header y fallará al arrancar.

        El CRC32 del trailer RKnano NO se recalcula intencionalmente:
        el Echo Mini no lo verifica y un CRC mal calculado podría bloquear
        el proceso de flasheo.
        """
        data = self.data
        if data[0x1F8:0x200] != b'RKnanoFW':
            return  # no es un firmware RKnano estándar, no tocar

        ir_off = struct.unpack_from('<I', data, 0x14C)[0]
        ir_sz  = struct.unpack_from('<I', data, 0x150)[0]
        p5_end = ir_off + ir_sz

        fw_end = struct.unpack_from('<I', data, 0x1F4)[0]
        if p5_end > fw_end:
            fw_end = ((p5_end + 0xFFFF) // 0x10000) * 0x10000
            struct.pack_into('<I', data, 0x1F4, fw_end)

        if fw_end + 0x200 <= len(data):
            data[fw_end:fw_end + 0x200] = data[0:0x200]

    # ── Guardar resultado ────────────────────────────────────────────────────

    def save(self, out_path: Path):
        out_path.write_bytes(self.data)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parche de boot por tema para el firmware del Echo Mini"
    )
    parser.add_argument("img", help="Ruta al archivo .IMG del firmware")
    parser.add_argument("-o", "--output", help="Ruta de salida (default: <nombre>_patched.IMG)")
    parser.add_argument("--check", action="store_true",
                        help="Solo verificar si el parche ya está aplicado, sin modificar nada")
    args = parser.parse_args()

    img_path = Path(args.img)
    if not img_path.exists():
        print(f"Error: no se encontró el archivo '{img_path}'")
        sys.exit(1)

    print(f"Cargando {img_path.name} …")
    fw = FirmwarePatcher(img_path)

    info = fw.detect()
    print(f"  Recursos  : {info['resource_count']}")
    print(f"  Bloque    : {info['old_block_size']} recursos/tema")
    print(f"  CMP offset: 0x{info['cmp_offset']:X}")
    print(f"  Estado    : {'✅ ya parcheado' if info['is_patched'] else '⬜ sin parchear'}")

    if args.check:
        return

    if info['is_patched']:
        print("\nEl firmware ya tiene el parche. No se realizaron cambios.")
        return

    out_path = Path(args.output) if args.output else img_path.with_stem(img_path.stem + "_patched")

    # Hacer backup automático del original
    backup = img_path.with_suffix(".IMG.bak")
    if not backup.exists():
        shutil.copy2(img_path, backup)
        print(f"\nBackup guardado en: {backup.name}")

    def progress(pct):
        filled = int(pct / 5)
        bar = "█" * filled + "░" * (20 - filled)
        print(f"\r  [{bar}] {pct:3d}%", end="", flush=True)

    print("\nAplicando parche …")
    result = fw.apply_patch(progress_fn=progress)
    print()

    fw.save(out_path)
    print(f"\n{result}")
    print(f"Archivo guardado en: {out_path}")


if __name__ == "__main__":
    main()
