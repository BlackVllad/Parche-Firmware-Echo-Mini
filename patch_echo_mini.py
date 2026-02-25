#!/usr/bin/env python3
"""
patch_echo_mini.py — Per-theme boot patch for the Echo Mini (RKnano)
=====================================================================

What it does
------------
Modifies the firmware .IMG so each theme has its own independent boot/shutdown
animation instead of sharing Theme A's animation.

Changes applied
---------------
1. Expands the ROCK26 table: 1602 → 1870 entries (5 blocks × 374)
2. Expands the metadata table with the same entries × 5
3. CMP R0, #0x43  →  CMP R0, #0x00
4. 4 × ADDW: [307, 614, 921, 1228] → [374, 748, 1122, 1496]
5. Updates fw_end and writes the header copy at the end of the file

Usage
-----
    python patch_echo_mini.py HIFIEC20.IMG
    python patch_echo_mini.py HIFIEC20.IMG -o firmware_patched.IMG
    python patch_echo_mini.py HIFIEC20.IMG --check

Requirements
------------
    Python 3.8+  (no external dependencies)
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
            raise ValueError("ROCK26IMAGERES not found in firmware.")

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
            raise ValueError("Metadata table not found.")

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
        data       = self.img_data
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
            raise ValueError("Theme dispatch CMP instruction not found.")

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
            raise ValueError(f"CMP found but only {len(addw_list)} ADDW instructions (need 4).")

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
            return "Firmware is already patched (CMP R0,#0x00 detected)."

        data      = self.img_data
        SHARED    = info['shared_count']
        OLD_BLK   = info['old_block_size']
        NEW_BLK   = info['new_block_size']
        cmp_off   = info['cmp_offset']
        addw_list = info['addw_list']

        part5     = self.get_part5()
        r26_start = self.rock26_start_in_part5
        old_count = self.rock26_count

        # Read shared resources (indices 0-66)
        shared_r26 = []
        for i in range(SHARED):
            eo = r26_start + i * 16
            shared_r26.append(bytes(part5[eo:eo + 16]))

        shared_meta_raw = []
        for i in range(SHARED):
            tp = self.entries[i]['table_pos']
            shared_meta_raw.append(bytes(part5[tp:tp + self.METADATA_ENTRY_SIZE]))

        # Build new tables: 5 blocks × NEW_BLK entries
        new_r26  = []
        new_meta = []
        theme_letters = ['A', 'B', 'C', 'D', 'E']

        for t_idx in range(5):
            letter = theme_letters[t_idx]
            # 67 shared boot copies (renamed T_X_ for themes B-E)
            for i in range(SHARED):
                new_r26.append(shared_r26[i])
                meta_raw = bytearray(shared_meta_raw[i])
                if t_idx > 0:
                    orig_name  = self.entries[i]['name']
                    new_name   = f"T_{letter}_{orig_name}"
                    name_bytes = new_name.encode('ascii')[:63]
                    meta_raw[32:96] = name_bytes + b'\x00' * (64 - len(name_bytes))
                new_meta.append(bytes(meta_raw))

            # OLD_BLK themed resources (icons, backgrounds, etc.)
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

        # Write expanded ROCK26 table
        r26_abs   = self.part5_offset + self.rock26_off_in_part5
        count_abs = r26_abs + 16
        struct.pack_into('<I', data, count_abs, new_count)
        entries_abs = self.part5_offset + r26_start
        for i, entry_raw in enumerate(new_r26):
            pos = entries_abs + i * 16
            if pos + 16 > len(data):
                data.extend(b'\x00' * (pos + 16 - len(data)))
            data[pos:pos + 16] = entry_raw

        # Write expanded metadata table
        meta_abs = self.part5_offset + self.table_start
        for i, meta_raw in enumerate(new_meta):
            pos = meta_abs + i * self.METADATA_ENTRY_SIZE
            if pos + self.METADATA_ENTRY_SIZE > len(data):
                data.extend(b'\x00' * (pos + self.METADATA_ENTRY_SIZE - len(data)))
            data[pos:pos + self.METADATA_ENTRY_SIZE] = meta_raw

        if progress_fn:
            progress_fn(50)

        # Update Part5 size in header
        new_p5_end      = (meta_abs + len(new_meta) * self.METADATA_ENTRY_SIZE) - self.part5_offset
        self.part5_size = new_p5_end
        struct.pack_into('<I', data, 0x150, new_p5_end)

        # Patch CMP R0,#0x43 → CMP R0,#0x00
        data[cmp_off:cmp_off + 2] = struct.pack('<H', 0x2800)

        # Patch 4 ADDW instructions
        new_addw_vals = [NEW_BLK * (i + 1) for i in range(4)]
        for i, (foff, _old_val) in enumerate(addw_list):
            data[foff:foff + 4] = self._encode_addw(new_addw_vals[i], rd=0, rn=0)

        if progress_fn:
            progress_fn(70)

        # Fix file integrity (fw_end, header copy, trailer)
        self._fix_integrity()

        if progress_fn:
            progress_fn(100)

        return (
            f"Patch applied successfully\n"
            f"   CMP  : 0x{cmp_off:X}  (0x2843 → 0x2800)\n"
            f"   ADDW : {[v for _, v in addw_list]} → {new_addw_vals}\n"
            f"   Table: {old_count} → {new_count} entries\n"
            f"   Block: {OLD_BLK} → {NEW_BLK} resources/theme\n"
        )

    def _fix_integrity(self):
        """Updates fw_end, extends the file if needed, and writes the header copy.

        The trailer (last 4 bytes) is preserved as-is — the Echo Mini does not
        actively verify the RKnano CRC32 trailer.
        """
        data = self.img_data
        if data[0x1F8:0x200] != b'RKnanoFW':
            return

        # Save trailer before any resize
        saved_trailer = bytes(data[-4:])

        # Recalculate fw_end from the actual end of Part5
        fw_end = struct.unpack_from('<I', data, 0x1F4)[0]
        ir_off = struct.unpack_from('<I', data, 0x14C)[0]
        ir_sz  = struct.unpack_from('<I', data, 0x150)[0]
        p5_end = ir_off + ir_sz

        if p5_end > fw_end:
            fw_end = ((p5_end + 0xFFFF) // 0x10000) * 0x10000
            struct.pack_into('<I', data, 0x1F4, fw_end)

        # Extend the file so fw_end is within bounds.
        # This fixes files that were patched/modified but not resized correctly —
        # the Echo Mini rejects them because the bootloader looks for the header
        # copy at fw_end and cannot find it.
        ALIGN   = 0x100000
        fw_size = ((fw_end + 16384 + ALIGN) // ALIGN) * ALIGN
        needed  = fw_size + 4

        if len(data) < needed:
            data.extend(b'\x00' * (needed - len(data)))
        elif len(data) > needed:
            del data[needed:]

        # Write 512-byte header copy at fw_end (required by the bootloader)
        data[fw_end:fw_end + 0x200] = data[0:0x200]

        # Restore trailer
        data[-4:] = saved_trailer

    def save(self, out_path: Path):
        Path(out_path).write_bytes(self.img_data)


def main():
    parser = argparse.ArgumentParser(
        description="Per-theme boot patch for the Echo Mini firmware (RKnano)"
    )
    parser.add_argument("img",    nargs="?", help="Path to the input .IMG file")
    parser.add_argument("output", nargs="?", help="Output path (default: <name>_patched.IMG)")
    parser.add_argument("--check", action="store_true",
                        help="Only check patch status, do not modify anything")
    args = parser.parse_args()

    if not args.img:
        parser.print_help()
        sys.exit(1)

    inp = Path(args.img)
    if not inp.exists():
        print(f"Error: file not found: '{inp}'")
        sys.exit(1)

    print(f"Loading {inp.name} ...")
    fw   = FirmwarePatcher(inp)
    info = fw.detect_patch_info()

    print(f"  Resources : {info['resource_count']}")
    print(f"  Block size: {info['old_block_size']} resources/theme")
    print(f"  CMP offset: 0x{info['cmp_offset']:X}  (value: 0x{info['cmp_value']:04X})")
    print(f"  ADDW      : {info['addw_values']}")
    print(f"  Status    : {'already patched' if info['is_patched'] else 'not patched'}")

    if args.check:
        return

    if info['is_patched']:
        print("\nFirmware is already patched. No changes made.")
        return

    out = Path(args.output) if args.output else inp.with_stem(inp.stem + "_patched")

    backup = inp.with_suffix(".IMG.bak")
    if not backup.exists():
        shutil.copy2(inp, backup)
        print(f"\nBackup saved to: {backup.name}")

    def progress(pct):
        filled = int(pct / 5)
        print(f"\r  [{'█' * filled}{'░' * (20 - filled)}] {pct:3d}%", end="", flush=True)

    print("\nApplying patch ...")
    result = fw.patch_for_themed_boots(progress_fn=progress)
    print()

    fw.save(out)
    print(f"\n{result}")
    print(f"Saved to: {out}")


if __name__ == "__main__":
    main()
