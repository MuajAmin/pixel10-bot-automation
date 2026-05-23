#!/usr/bin/env python3
import os
import sys
import argparse

def patch_binary(filepath, search_str, replace_str, backup=True):
    if not os.path.exists(filepath):
        print(f"Error: File '{filepath}' does not exist.")
        return False

    search_bytes = search_str.encode('ascii')
    replace_bytes = replace_str.encode('ascii')

    if len(replace_bytes) > len(search_bytes):
        print(f"Error: Replacement string length ({len(replace_bytes)}) is longer than the search string length ({len(search_bytes)}).")
        print("Replacement must be equal to or shorter than the target to preserve ELF offsets.")
        return False

    # Read binary content
    with open(filepath, 'rb') as f:
        data = bytearray(f.read())

    # Find the target byte offset
    offset = data.find(search_bytes)
    if offset == -1:
        print(f"Error: Target byte sequence '{search_str}' not found in the binary.")
        return False

    # Format replacement bytes: pad with null bytes to match the original size exactly
    padded_replacement = replace_bytes + b'\x00' * (len(search_bytes) - len(replace_bytes))

    print(f"Found target sequence at offset: {hex(offset)}")
    print(f"Replacing '{search_str}' with '{replace_str}' (padded with {len(search_bytes) - len(replace_bytes)} null bytes)")

    # Write backup
    if backup:
        backup_path = filepath + '.bak'
        try:
            with open(backup_path, 'wb') as f:
                f.write(data)
            print(f"Backup saved to: {backup_path}")
        except Exception as e:
            print(f"Error creating backup file: {e}")
            return False

    # Apply patch
    data[offset:offset + len(search_bytes)] = padded_replacement

    # Write patched file back
    try:
        with open(filepath, 'wb') as f:
            f.write(data)
        print(f"Successfully patched: {filepath}")
        return True
    except Exception as e:
        print(f"Error writing patched binary: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Static Binary Patcher for Android graphics libraries (ELF files)")
    parser.add_argument("--file", default="/vendor/lib64/egl/libGLES_swiftshader.so", help="Path to the binary file to patch")
    parser.add_argument("--search", default="Google SwiftShader", help="ASCII string to search for")
    parser.add_argument("--replace", default="Adreno (TM) 830", help="ASCII string to replace with")
    parser.add_argument("--no-backup", action="store_true", help="Disable creation of a .bak file before patching")

    args = parser.parse_args()

    success = patch_binary(args.file, args.search, args.replace, not args.no_backup)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
