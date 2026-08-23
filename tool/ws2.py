"""CROSS†CHANNEL .ws2 script codec.

Obfuscation is a per-byte rotate-left-6 (verified: round-trips byte-exactly,
and exposes ASCII opcode operands such as script names and CG filenames).

Relevant opcodes (operand forms confirmed against Steam story scripts):

    0x34  <slot>\0 <FILE.PNA>\0 0x01 0x01     display a CG into a layer slot
    0x33  <slot>\0 <FILE.PNG>\0 0x01 0x01     hard-load resource (background)
    0x66  ...                                  effect mask (tolerates missing)
    0x0b  <u16 var> 0x01                      push/set a variable
    0x04  <NAME>\0                            call another script by name
    0x07  <NAME>\0                            jump to another script (no return)
"""
import re
import struct


def decode(raw):
    """Deobfuscate a .ws2 member (rotate left 6)."""
    return bytes(((c << 6) | (c >> 2)) & 0xff for c in raw)


def encode(data):
    """Re-obfuscate a .ws2 member (inverse: rotate right 6 == left 2)."""
    return bytes(((c << 2) | (c >> 6)) & 0xff for c in data)


# 0x34 <slot> 00 <STEM>.PNA 00 01 01
DISPLAY_PNA = re.compile(
    rb'\x34([A-Za-z0-9_]{1,12})\x00([A-Za-z0-9_]+)\.PNA\x00\x01\x01', re.I)

# 0x33 <slot> 00 <FILE>.PNG 00 01 01
DISPLAY_PNG = re.compile(
    rb'\x33([A-Za-z0-9_]{1,12})\x00([A-Za-z0-9_]+)\.PNG\x00\x01\x01', re.I)

# 0x04 <NAME> 00 (call)
CALL = re.compile(rb'\x04([A-Z0-9_]+)\x00')

# 0x07 <NAME> 00 (jump)
JUMP = re.compile(rb'\x07([A-Z0-9_]+)\x00')


def extract_pna_refs(decoded_data):
    """Extract all PNA file stems referenced in decoded script."""
    return [m.group(2).decode('ascii') for m in DISPLAY_PNA.finditer(decoded_data)]


def extract_png_refs(decoded_data):
    """Extract all PNG file names referenced in decoded script."""
    return [m.group(2).decode('ascii') for m in DISPLAY_PNG.finditer(decoded_data)]


def extract_calls(decoded_data):
    """Extract all script names called via 0x04 (call)."""
    return [m.group(1).decode('ascii') for m in CALL.finditer(decoded_data)]


def extract_jumps(decoded_data):
    """Extract all script names jumped to via 0x07 (jump)."""
    return [m.group(1).decode('ascii') for m in JUMP.finditer(decoded_data)]


def extract_all_jumps(decoded_data):
    """Extract all script jumps (both call and jump)."""
    return extract_calls(decoded_data) + extract_jumps(decoded_data)
