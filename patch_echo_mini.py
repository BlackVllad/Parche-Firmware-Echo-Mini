#!/usr/bin/env python3
"""
patch_echo_mini.py — Parche de boot por tema para el Echo Mini (RKnano)
=======================================================================

Qué hace
--------
Modifica el firmware .IMG para que cada tema tenga su propia animación
de encendido/apagado independiente, en lugar de compartir la del Tema A.

Cambios que aplica
------------------
1. Expande la tabla ROCK26: 1602 → 1870 entradas (5 bloques × 374)
2. Expande la tabla de metadatos con las mismas entradas × 5
3. CMP R0, #0x43  →  CMP R0, #0x00
4. 4 × ADDW: [307, 614, 921, 1228] → [374, 748, 1122, 1496]
5. Actualiza fw_end y escribe la header copy al final del archivo

Uso
---
    python patch_echo_mini.py HIFIEC20.IMG
    python patch_echo_mini.py HIFIEC20.IMG -o firmware_modificado.IMG
    python patch_echo_mini.py HIFIEC20.IMG --check

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
METADATA_ENTRY_SIZE = 108
SHARED_COUNT        = 67


class FirmwarePatcher:

    METADATA_ENTRY_SIZE = 108

    def __init__(self, img_path: Path):
        self.img_path = Path(img_path)
        self.img_data = bytearray(self.img_path.read_bytes())
        self._parse()

    def _parse(self):
        data = self.img_data
        info = struct.unpack('<IIII', data[0x14C:0x15C])
        self.part5_offset = info[0]
        self.part5_size   = info[1]

        part5 = self.get_part5()

        rock26_off = bytes(part5).find(b'ROCK26IMAGERES')
        if rock26_off == -1:
            raise ValueError("No se encontró ROCK26IMAGERES en el firmware.")

        self.rock26_off_in_part5   = rock26_off
        self.rock26_start_in_part5 = rock26_off + 32
        self.rock26_off            = rock26_off
        self.rock26_start          = rock26_off + 32
        self.rock26_count          = struct.unpack('<I', part5[rock26_off + 16:rock26_off + 20])[0]

        anchor      = struct.unpack('<I', part5[self.rock26_start + 12:self.rock26_start + 16])[0]
        first_match = None
        for pos in range(0, len(part5) - self.METADATA_ENTRY_SIZE, 4):
            eoff = struct.unpack('<I', part5[pos + 20:pos + 24])[0]
            if eoff == anchor:
                nm = bytes(part5[pos + 32:pos + 96]).split(b'\x00')[0].decode('ascii', errors='ignore')
                if nm.endswith('.BMP') and len(nm) >= 5:
                    first_match = pos
                    break

        if first_match is None:
            raise ValueError("No se encontró la tabla de metadatos.")

        table_start = first_match
        while table_start >= self.METADATA_ENTRY_SIZE:
            tp = table_start - self.METADATA_ENTRY_SIZE
            tn = bytes(part5[tp + 32:tp + 96]).split(b'\x00')[0].decode('ascii', errors='ignore')
            if tn and tn.endswith('.BMP') and len(tn) >= 3:
                table_start = tp
            else:
                break

        self.entries = []
        pos = table_start
        while pos + self.METADATA_ENTRY_SIZE <= len(part5):
            nm = bytes(part5[pos + 32:pos + 96]).split(b'\x00')[0].decode('ascii', errors='ignore')
            if not nm or len(nm) < 3:
                break
            off = struct.unpack('<I', part5[pos + 20:pos + 24])[0]
            w   = struct.unpack('<I', part5[pos + 24:pos + 28])[0]
            h   = struct.unpack('<I', part5[pos + 28:pos + 32])[0]
            self.entries.append({'name': nm, 'offset': off, 'width': w, 'height': h, 'table_pos': pos})
            pos += self.METADATA_ENTRY_SIZE

        self.table_start = table_start

    def get_part5(self):
        return memoryview(self.img_data)[self.part5_offset:self.part5_offset + self.part5_size]

    @staticmethod
    def _decode_addw(hw1, hw2) -> int:
        i    = (hw1 >> 10) & 1
        imm3 = (hw2 >> 12) & 0x7
        imm8 =  hw2 & 0xFF
        return (i << 11) | (imm3 << 8) | imm8

    @staticmethod
    def _encode_addw(imm12: int, rd: int = 0, rn: int = 0) -> bytes:
        assert 0 <= imm12 < 4096
        i    = (imm12 >> 11) & 1
        imm3 = (imm12 >> 8) & 0x7
        imm8 =  imm12 & 0xFF
        hw1  = 0xF200 | (i << 10) | rn
        hw2  = (imm3 << 12) | (rd << 8) | imm8
        return struct.pack('<HH', hw1, hw2)

    def detect_patch_info(self) -> dict:
        data = self.img_data
        cmp_offset = None

        for off in range(0x200, min(len(data), 0x400000), 2):
            val = struct.unpack_from('<H', data, off)[0]
            if val == 0x2843 or val == 0x2800:
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
            block_size     = addw_values[0]
            old_block_size = block_size - SHARED_COUNT
        else:
            old_block_size = addw_values[0]

        return {
            'cmp_offset':     cmp_offset,
            'cmp_value':      cmp_val,
            'is_patched':     is_patched,
            'addw_list':      addw_list,
            'addw_values':    addw_values,
            'old_block_size': old_block_size,
            'shared_count':   SHARED_COUNT,
            'new_block_size': old_block_size + SHARED_COUNT,
            'resource_count': self.rock26_count,
        }

    def patch_for_themed_boots(self, progress_fn=None) -> str:
        info = self.detect_patch_info()
        if info['is_patched']:
            return "El firmware ya tiene el parche aplicado (CMP R0,#0x00 detectado)."

        data      = self.img_data
        SHARED    = info['shared_count']
        OLD_BLK   = info['old_block_size']
        NEW_BLK   = info['new_block_size']
        cmp_off   = info['cmp_offset']
        addw_list = info['addw_list']

        part5     = self.get_part5()
        r26_start = self.rock26_start_in_part5
        old_count = self.rock26_count

        shared_r26 = []
        for i in range(SHARED):
            eo = r26_start + i * 16
            shared_r26.append(bytes(part5[eo:eo + 16]))

        shared_meta_raw = []
        for i in range(SHARED):
            tp = self.entries[i]['table_pos']
            shared_meta_raw.append(bytes(part5[tp:tp + self.METADATA_ENTRY_SIZE]))

        new_r26  = []
        new_meta = []
        theme_letters = ['A', 'B', 'C', 'D', 'E']

        for t_idx in range(5):
            letter = theme_letters[t_idx]
            for i in range(SHARED):
                new_r26.append(shared_r26[i])
                meta_raw = bytearray(shared_meta_raw[i])
                if t_idx > 0:
                    orig_name  = self.entries[i]['name']
                    new_name   = f"T_{letter}_{orig_name}"
                    name_bytes = new_name.encode('ascii')[:63]
                    meta_raw[32:96] = name_bytes + b'\x00' * (64 - len(name_bytes))
                new_meta.append(bytes(meta_raw))

            old_start = SHARED + t_idx * OLD_BLK
            for i in range(OLD_BLK):
                src_idx = old_start + i
                if src_idx < old_count:
                    eo = r26_start + src_idx * 16
                    new_r26.append(bytes(part5[eo:eo + 16]))
                else:
                    new_r26.append(shared_r26[0])
                if src_idx < len(self.entries):
                    tp = self.entries[src_idx]['table_pos']
                    new_meta.append(bytes(part5[tp:tp + self.METADATA_ENTRY_SIZE]))
                else:
                    new_meta.append(shared_meta_raw[0])

            if progress_fn:
                progress_fn(int((t_idx + 1) * 14))

        new_count = len(new_r26)

        r26_abs   = self.part5_offset + self.rock26_off_in_part5
        count_abs = r26_abs + 16
        struct.pack_into('<I', data, count_abs, new_count)
        entries_abs = self.part5_offset + r26_start
        for i, entry_raw in enumerate(new_r26):
            pos = entries_abs + i * 16
            if pos + 16 > len(data):
                data.extend(b'\x00' * (pos + 16 - len(data)))
            data[pos:pos + 16] = entry_raw

        meta_abs = self.part5_offset + self.table_start
        for i, meta_raw in enumerate(new_meta):
            pos = meta_abs + i * self.METADATA_ENTRY_SIZE
            if pos + self.METADATA_ENTRY_SIZE > len(data):
                data.extend(b'\x00' * (pos + self.METADATA_ENTRY_SIZE - len(data)))
            data[pos:pos + self.METADATA_ENTRY_SIZE] = meta_raw

        if progress_fn:
            progress_fn(50)

        new_p5_end      = (meta_abs + len(new_meta) * self.METADATA_ENTRY_SIZE) - self.part5_offset
        self.part5_size = new_p5_end
        struct.pack_into('<I', data, 0x150, new_p5_end)

        data[cmp_off:cmp_off + 2] = struct.pack('<H', 0x2800)

        new_addw_vals = [NEW_BLK * (i + 1) for i in range(4)]
        for i, (foff, _old_val) in enumerate(addw_list):
            data[foff:foff + 4] = self._encode_addw(new_addw_vals[i], rd=0, rn=0)

        if progress_fn:
            progress_fn(70)

        self._fix_integrity()

        if progress_fn:
            progress_fn(100)

        return (
            f"✅ Parche aplicado correctamente\n"
            f"   CMP  : 0x{cmp_off:X}  (0x2843 → 0x2800)\n"
            f"   ADDW : {[v for _, v in addw_list]} → {new_addw_vals}\n"
            f"   Tabla: {old_count} → {new_count} entradas\n"
            f"   Bloque: {OLD_BLK} → {NEW_BLK} recursos/tema\n"
        )

    def _fix_integrity(self):
        """Actualiza fw_end, extiende el archivo y escribe la header copy.

        El trailer (últimos 4 bytes) se preserva — el Echo Mini no verifica
        activamente el CRC32 del trailer RKnano.
        """
        data = self.img_data
        if data[0x1F8:0x200] != b'RKnanoFW':
            return

        saved_trailer = bytes(data[-4:])

        fw_end = struct.unpack_from('<I', data, 0x1F4)[0]
        ir_off = struct.unpack_from('<I', data, 0x14C)[0]
        ir_sz  = struct.unpack_from('<I', data, 0x150)[0]
        p5_end = ir_off + ir_sz

        if p5_end > fw_end:
            fw_end = ((p5_end + 0xFFFF) // 0x10000) * 0x10000
            struct.pack_into('<I', data, 0x1F4, fw_end)

        ALIGN   = 0x100000
        fw_size = ((fw_end + 16384 + ALIGN) // ALIGN) * ALIGN
        needed  = fw_size + 4

        if len(data) < needed:
            data.extend(b'\x00' * (needed - len(data)))
        elif len(data) > needed:
            del data[needed:]

        data[fw_end:fw_end + 0x200] = data[0:0x200]
        data[-4:] = saved_trailer

    def save(self, out_path: Path):
        Path(out_path).write_bytes(self.img_data)


def main():
    parser = argparse.ArgumentParser(
        description="Parche de boot por tema para el firmware del Echo Mini (RKnano)"
    )
    parser.add_argument("img",    nargs="?", help="Ruta al .IMG de entrada")
    parser.add_argument("output", nargs="?", help="Ruta de salida (default: <nombre>_patched.IMG)")
    parser.add_argument("--check", action="store_true",
                        help="Solo verificar estado del parche, sin modificar nada")
    args = parser.parse_args()

    if not args.img:
        parser.print_help()
        sys.exit(1)

    inp = Path(args.img)
    if not inp.exists():
        print(f"Error: no se encontró '{inp}'")
        sys.exit(1)

    print(f"Cargando {inp.name} …")
    fw   = FirmwarePatcher(inp)
    info = fw.detect_patch_info()

    print(f"  Recursos  : {info['resource_count']}")
    print(f"  Bloque    : {info['old_block_size']} recursos/tema")
    print(f"  CMP offset: 0x{info['cmp_offset']:X}  (valor: 0x{info['cmp_value']:04X})")
    print(f"  ADDW      : {info['addw_values']}")
    print(f"  Estado    : {'✅ ya parcheado' if info['is_patched'] else '⬜ sin parchear'}")

    if args.check:
        return

    if info['is_patched']:
        print("\nEl firmware ya tiene el parche. No se realizaron cambios.")
        return

    out = Path(args.output) if args.output else inp.with_stem(inp.stem + "_patched")

    backup = inp.with_suffix(".IMG.bak")
    if not backup.exists():
        shutil.copy2(inp, backup)
        print(f"\nBackup guardado en: {backup.name}")

    def progress(pct):
        filled = int(pct / 5)
        print(f"\r  [{'█' * filled}{'░' * (20 - filled)}] {pct:3d}%", end="", flush=True)

    print("\nAplicando parche …")
    result = fw.patch_for_themed_boots(progress_fn=progress)
    print()

    fw.save(out)
    print(f"\n{result}")
    print(f"Guardado en: {out}")


if __name__ == "__main__":
    main()
