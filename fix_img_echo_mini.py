#!/usr/bin/env python3
"""
fix_img_echo_mini.py — Reparar un .IMG que el Echo Mini rechaza
================================================================

Úsalo cuando:
  - El dispositivo detecta el archivo de update pero lo cancela en 1-3 segundos
  - El .IMG fue parcheado/modificado pero fw_end apunta fuera del archivo
  - El trailer está corrupto (ej: c618c618 en lugar de un valor válido)

Correcciones que aplica
-----------------------
1. Recalcula fw_end desde el final real de Part5
2. Extiende el archivo al tamaño necesario para que fw_end quede dentro de él
3. Escribe la copia del header (512 bytes) en fw_end  ← el bootloader la requiere
4. Preserva el trailer original (el dispositivo no verifica el CRC32)

Uso
---
    python fix_img_echo_mini.py firmware.IMG
    python fix_img_echo_mini.py firmware.IMG -o firmware_fixed.IMG
    python fix_img_echo_mini.py firmware.IMG --info

Requisitos
----------
    Python 3.8+  (sin dependencias externas)
"""

import sys
import struct
import shutil
import argparse
from pathlib import Path


class FirmwareFixer:

    def __init__(self, img_path: Path):
        self.img_path = Path(img_path)
        self.img_data = bytearray(self.img_path.read_bytes())
        self._check_magic()

    def _check_magic(self):
        if self.img_data[0x1F8:0x200] != b'RKnanoFW':
            raise ValueError("No es un firmware RKnano válido (falta el magic 'RKnanoFW' en 0x1F8).")

    def get_info(self) -> dict:
        """Devuelve el estado actual del archivo (fw_end, tamaño, header copy, trailer)."""
        data      = self.img_data
        fw_end    = struct.unpack_from('<I', data, 0x1F4)[0]
        p5_off    = struct.unpack_from('<I', data, 0x14C)[0]
        p5_sz     = struct.unpack_from('<I', data, 0x150)[0]
        p5_end    = p5_off + p5_sz
        trailer   = bytes(data[-4:]).hex()
        inside    = (fw_end + 0x200) <= len(data)
        header_ok = inside and (data[fw_end:fw_end + 0x200] == data[0:0x200])

        return {
            'fw_end':    fw_end,
            'p5_end':    p5_end,
            'file_size': len(data),
            'trailer':   trailer,
            'inside':    inside,
            'header_ok': header_ok,
        }

    def fix(self) -> str:
        """
        Repara la integridad del .IMG:
          ① Preserva el trailer
          ② Recalcula fw_end desde el final real de Part5
          ③ Extiende/recorta el archivo al tamaño necesario
          ④ Escribe la header copy en fw_end
          ⑤ Restaura el trailer
        """
        data = self.img_data

        old_fw_end  = struct.unpack_from('<I', data, 0x1F4)[0]
        old_size    = len(data)
        old_trailer = bytes(data[-4:]).hex()

        # ① Preservar trailer antes de modificar el archivo
        saved_trailer = bytes(data[-4:])

        # ② Recalcular fw_end desde el final real de Part5
        p5_off = struct.unpack_from('<I', data, 0x14C)[0]
        p5_sz  = struct.unpack_from('<I', data, 0x150)[0]
        p5_end = p5_off + p5_sz
        fw_end = old_fw_end

        if p5_end > fw_end:
            fw_end = ((p5_end + 0xFFFF) // 0x10000) * 0x10000
            struct.pack_into('<I', data, 0x1F4, fw_end)

        # ③ Extender el archivo para que fw_end quede dentro
        ALIGN   = 0x100000
        fw_size = ((fw_end + 16384 + ALIGN) // ALIGN) * ALIGN
        needed  = fw_size + 4

        if len(data) < needed:
            data.extend(b'\x00' * (needed - len(data)))
        elif len(data) > needed:
            del data[needed:]

        # ④ Escribir header copy en fw_end (el bootloader del Echo Mini la busca aquí)
        data[fw_end:fw_end + 0x200] = data[0:0x200]

        # ⑤ Restaurar trailer
        data[-4:] = saved_trailer

        new_fw_end  = struct.unpack_from('<I', data, 0x1F4)[0]
        new_size    = len(data)
        new_trailer = bytes(data[-4:]).hex()
        header_ok   = (data[new_fw_end:new_fw_end + 0x200] == data[0:0x200])

        return (
            f"✅ Integridad del firmware corregida\n\n"
            f"   fw_end     : 0x{old_fw_end:X} → 0x{new_fw_end:X}\n"
            f"   Tamaño     : {old_size:,} → {new_size:,} bytes "
            f"({(new_size - old_size) // 1024:+,} KB)\n"
            f"   Header copy: {'✓ escrita en fw_end' if header_ok else '✗ error al escribir'}\n"
            f"   Trailer    : {old_trailer} → {new_trailer}\n"
        )

    def save(self, out_path: Path):
        Path(out_path).write_bytes(self.img_data)


def main():
    parser = argparse.ArgumentParser(
        description="Reparar un .IMG del Echo Mini que es rechazado al intentar actualizar"
    )
    parser.add_argument("img",    help="Ruta al .IMG de entrada")
    parser.add_argument("-o", "--output", help="Ruta de salida (default: <nombre>_fixed.IMG)")
    parser.add_argument("--info", action="store_true",
                        help="Mostrar información del archivo sin reparar nada")
    args = parser.parse_args()

    inp = Path(args.img)
    if not inp.exists():
        print(f"Error: no se encontró '{inp}'")
        sys.exit(1)

    print(f"Cargando {inp.name} …")
    fw   = FirmwareFixer(inp)
    info = fw.get_info()

    print(f"  Tamaño  : {info['file_size']:,} bytes")
    print(f"  fw_end  : 0x{info['fw_end']:X}  ({'dentro del archivo ✓' if info['inside'] else 'FUERA del archivo ✗'})")
    print(f"  Part5   : termina en 0x{info['p5_end']:X}")
    print(f"  Header copy en fw_end: {'✓' if info['header_ok'] else '✗ no encontrada'}")
    print(f"  Trailer : {info['trailer']}")

    if args.info:
        if info['inside'] and info['header_ok']:
            print("\n✅ El archivo parece estar en buen estado.")
        else:
            print("\n⚠ El archivo tiene problemas de integridad. Usa --fix para repararlo.")
            print("   (o ejecuta sin --info para reparar directamente)")
        return

    out = Path(args.output) if args.output else inp.with_stem(inp.stem + "_fixed")

    backup = inp.with_suffix(".IMG.bak")
    if not backup.exists():
        shutil.copy2(inp, backup)
        print(f"\nBackup guardado en: {backup.name}")

    result = fw.fix()
    fw.save(out)
    print(f"\n{result}")
    print(f"Guardado en: {out}")


if __name__ == "__main__":
    main()
