#!/usr/bin/env python3
"""
fix_img_echo_mini.py — Repair an Echo Mini .IMG rejected during update
=======================================================================

Use this when:
  - The device detects the update file but cancels it within 1-3 seconds
  - The .IMG was patched/modified but fw_end points outside the file
  - The trailer is corrupted (e.g. c618c618 instead of a valid value)

Fixes applied
-------------
1. Recalculates fw_end from the actual end of Part5
2. Extends the file so fw_end is within bounds
3. Writes the 512-byte header copy at fw_end  ← the bootloader requires this
4. Preserves the original trailer (the device does not verify the CRC32)

Usage
-----
    python fix_img_echo_mini.py firmware.IMG
    python fix_img_echo_mini.py firmware.IMG -o firmware_fixed.IMG
    python fix_img_echo_mini.py firmware.IMG --info

Requirements
------------
    Python 3.8+  (no external dependencies)
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
            raise ValueError("Not a valid RKnano firmware (missing 'RKnanoFW' magic at 0x1F8).")

    def get_info(self) -> dict:
        """Returns the current state of the file (fw_end, size, header copy, trailer)."""
        data    = self.img_data
        fw_end  = struct.unpack_from('<I', data, 0x1F4)[0]
        p5_off  = struct.unpack_from('<I', data, 0x14C)[0]
        p5_sz   = struct.unpack_from('<I', data, 0x150)[0]
        p5_end  = p5_off + p5_sz
        trailer = bytes(data[-4:]).hex()
        inside  = (fw_end + 0x200) <= len(data)
        hdr_ok  = inside and (data[fw_end:fw_end + 0x200] == data[0:0x200])

        return {
            'fw_end':    fw_end,
            'p5_end':    p5_end,
            'file_size': len(data),
            'trailer':   trailer,
            'inside':    inside,
            'header_ok': hdr_ok,
        }

    def fix(self) -> str:
        """
        Repairs the .IMG integrity:
          Step 1 — Save the trailer before any resize
          Step 2 — Recalculate fw_end from the actual end of Part5
          Step 3 — Extend/trim the file to the required size
          Step 4 — Write the header copy at fw_end
          Step 5 — Restore the trailer
        """
        data = self.img_data

        old_fw_end  = struct.unpack_from('<I', data, 0x1F4)[0]
        old_size    = len(data)
        old_trailer = bytes(data[-4:]).hex()

        # Step 1: save trailer
        saved_trailer = bytes(data[-4:])

        # Step 2: recalculate fw_end from actual Part5 end
        p5_off = struct.unpack_from('<I', data, 0x14C)[0]
        p5_sz  = struct.unpack_from('<I', data, 0x150)[0]
        p5_end = p5_off + p5_sz
        fw_end = old_fw_end

        if p5_end > fw_end:
            fw_end = ((p5_end + 0xFFFF) // 0x10000) * 0x10000
            struct.pack_into('<I', data, 0x1F4, fw_end)

        # Step 3: extend the file so fw_end is within bounds
        ALIGN   = 0x100000
        fw_size = ((fw_end + 16384 + ALIGN) // ALIGN) * ALIGN
        needed  = fw_size + 4

        if len(data) < needed:
            data.extend(b'\x00' * (needed - len(data)))
        elif len(data) > needed:
            del data[needed:]

        # Step 4: write header copy at fw_end (bootloader requires it here)
        data[fw_end:fw_end + 0x200] = data[0:0x200]

        # Step 5: restore trailer
        data[-4:] = saved_trailer

        new_fw_end  = struct.unpack_from('<I', data, 0x1F4)[0]
        new_size    = len(data)
        new_trailer = bytes(data[-4:]).hex()
        header_ok   = (data[new_fw_end:new_fw_end + 0x200] == data[0:0x200])

        return (
            f"Firmware integrity fixed\n\n"
            f"   fw_end     : 0x{old_fw_end:X} → 0x{new_fw_end:X}\n"
            f"   Size       : {old_size:,} → {new_size:,} bytes "
            f"({(new_size - old_size) // 1024:+,} KB)\n"
            f"   Header copy: {'written at fw_end' if header_ok else 'ERROR writing'}\n"
            f"   Trailer    : {old_trailer} → {new_trailer}\n"
        )

    def save(self, out_path: Path):
        Path(out_path).write_bytes(self.img_data)


def main():
    parser = argparse.ArgumentParser(
        description="Repair an Echo Mini .IMG file that is rejected during device update"
    )
    parser.add_argument("img", help="Path to the input .IMG file")
    parser.add_argument("-o", "--output", help="Output path (default: <name>_fixed.IMG)")
    parser.add_argument("--info", action="store_true",
                        help="Show file information without repairing")
    args = parser.parse_args()

    inp = Path(args.img)
    if not inp.exists():
        print(f"Error: file not found: '{inp}'")
        sys.exit(1)

    print(f"Loading {inp.name} ...")
    fw   = FirmwareFixer(inp)
    info = fw.get_info()

    print(f"  Size       : {info['file_size']:,} bytes")
    print(f"  fw_end     : 0x{info['fw_end']:X}  ({'inside file' if info['inside'] else 'OUTSIDE file — will be rejected'})")
    print(f"  Part5 end  : 0x{info['p5_end']:X}")
    print(f"  Header copy: {'found' if info['header_ok'] else 'missing'}")
    print(f"  Trailer    : {info['trailer']}")

    if args.info:
        if info['inside'] and info['header_ok']:
            print("\nFile appears to be in good shape.")
        else:
            print("\nWarning: integrity issues detected. Run without --info to repair.")
        return

    out = Path(args.output) if args.output else inp.with_stem(inp.stem + "_fixed")

    backup = inp.with_suffix(".IMG.bak")
    if not backup.exists():
        shutil.copy2(inp, backup)
        print(f"\nBackup saved to: {backup.name}")

    result = fw.fix()
    fw.save(out)
    print(f"\n{result}")
    print(f"Saved to: {out}")


if __name__ == "__main__":
    main()
