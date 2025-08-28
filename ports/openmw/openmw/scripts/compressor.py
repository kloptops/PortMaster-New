#!/usr/bin/env python3
# Netch Leather Compressor - Texture Discovery & Compression
# Discovers, catalogs, and compresses Morrowind textures to KTX/ASTC format.

import argparse
import os
import subprocess
import json
import sys
import shutil
from collections import OrderedDict

# --- Constants ---
MOD_NAME = "Netch Leather Compressor"
SUPPORTED_EXTENSIONS = ('.tga', '.bmp', '.dds')
DB_FILENAME = 'texture_db.json'
TEMP_DIR_NAME = '.temp'

# --- Utility Functions ---

def normalize_path(path_str):
    """Normalizes a path to lowercase and uses Unix-style (forward) separators."""
    return path_str.strip().replace('\\', '/').lower()

def run_command(cmd, verbose=False, capture_binary=False):
    """Runs a shell command and returns its stdout. Exits on error."""
    if verbose:
        print(f"  Executing: {' '.join(cmd)}")
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=not capture_binary  # If capturing binary, don't decode as text
        )
        return process.stdout
    except subprocess.CalledProcessError as e:
        stderr_output = e.stderr.decode('utf-8', errors='ignore') if isinstance(e.stderr, bytes) else e.stderr
        print(f"\nError executing command: {' '.join(cmd)}", file=sys.stderr)
        print(f"Command failed with exit code {e.returncode}", file=sys.stderr)
        print(f"Stderr: {stderr_output.strip()}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"\nError: Command '{cmd[0]}' not found.", file=sys.stderr)
        print("Please ensure bsatool, minimg, and astcenc-neon are installed and in your system's PATH.", file=sys.stderr)
        sys.exit(1)

def unpack_bsa_file(bsa_path, file_in_bsa, dest_path, verbose=False):
    """Unpacks a single file from a BSA archive to a destination path."""
    if verbose:
        print(f"  Unpacking '{file_in_bsa}' from '{os.path.basename(bsa_path)}'...")
    try:
        # 'bsatool unpack' prints the raw file content to stdout
        file_content = run_command(['bsatool', 'unpack', bsa_path, file_in_bsa], verbose, capture_binary=True)
        with open(dest_path, 'wb') as f:
            f.write(file_content)
        return True
    except Exception as e:
        print(f"\nWarning: Failed to unpack '{file_in_bsa}' from '{bsa_path}'. Error: {e}")
        return False

def get_image_info_minimg(file_path, verbose=False):
    """Uses minimg to get image dimensions and other info from its JSON output."""
    try:
        json_output = run_command(['minimg', 'info', file_path], verbose)
        info = json.loads(json_output)
        return info.get('width'), info.get('height')
    except (json.JSONDecodeError, KeyError) as e:
        print(f"\nWarning: Could not parse metadata for '{file_path}'. Error: {e}")
        return None, None

# --- Core Logic ---

def parse_openmw_cfg(cfg_path):
    """Parses openmw.cfg to extract data and fallback-archive paths."""
    data_paths, fallback_archives = [], []
    cfg_dir = os.path.dirname(os.path.abspath(cfg_path))
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(('#', ';')): continue
                key, _, value = line.partition('=')
                key = key.strip().lower()
                path = value.strip().strip('"')
                if path:
                    abs_path = os.path.abspath(os.path.join(cfg_dir, path))
                    if key == 'data': data_paths.append(abs_path)
                    elif key == 'fallback-archive': fallback_archives.append(abs_path)
    except FileNotFoundError:
        print(f"Error: openmw.cfg not found at '{cfg_path}'", file=sys.stderr)
        sys.exit(1)
    return data_paths, fallback_archives

def discover_files(data_paths, fallback_archives, verbose=False):
    """Scans data directories and BSA archives, handling override priority."""
    discovered = OrderedDict()
    # 1. Scan data directories in reverse for correct priority
    for data_root in reversed(data_paths):
        if not os.path.isdir(data_root): continue
        if verbose: print(f"Scanning data directory: {data_root}")
        for root, _, files in os.walk(data_root):
            for filename in files:
                if filename.lower().endswith(SUPPORTED_EXTENSIONS):
                    full_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(full_path, data_root)
                    discovered[normalize_path(rel_path)] = {
                        'full_path': full_path, 'source_type': 'data_file',
                        'data_root': data_root, 'file_size': os.path.getsize(full_path)
                    }
    # 2. Scan BSA archives (lowest priority)
    for bsa_path in fallback_archives:
        if not os.path.isfile(bsa_path): continue
        if verbose: print(f"Scanning BSA archive: {bsa_path}")
        file_list_raw = run_command(['bsatool', 'list', bsa_path], verbose)
        for file_path_raw in file_list_raw.splitlines():
            if file_path_raw.lower().endswith(SUPPORTED_EXTENSIONS):
                key = normalize_path(file_path_raw)
                if key not in discovered:
                    discovered[key] = {
                        'full_path': f"[BSA]{bsa_path}{os.path.sep}{key}", 'source_type': 'bsa_file',
                        'bsa_path': bsa_path, 'file_size': 0
                    }
    return discovered

def populate_metadata(discovered_textures, temp_dir, verbose=False):
    """Populates dimensions for all textures, unpacking from BSA if necessary."""
    total = len(discovered_textures)
    for i, (rel_path, info) in enumerate(discovered_textures.items()):
        sys.stdout.write(f"\r  Processing metadata: {i+1}/{total} ({rel_path[:50]})")
        sys.stdout.flush()
        if info['source_type'] == 'data_file':
            info['dimensions'] = get_image_info_minimg(info['full_path'], verbose)
        else: # BSA file
            temp_texture_path = os.path.join(temp_dir, os.path.basename(rel_path))
            if unpack_bsa_file(info['bsa_path'], rel_path, temp_texture_path, verbose):
                info['dimensions'] = get_image_info_minimg(temp_texture_path, verbose)
                os.remove(temp_texture_path) # Clean up immediately
            else:
                info['dimensions'] = (None, None)
    sys.stdout.write("\n")

def compress_textures(texture_list, output_mod_root, temp_dir, args):
    """Converts and compresses textures using minimg and astcenc."""
    total = len(texture_list)
    for i, (rel_path, info) in enumerate(texture_list.items()):
        base_rel_path, _ = os.path.splitext(rel_path)
        final_ktx_path = os.path.join(output_mod_root, base_rel_path + '.ktx')
        
        progress_msg = f"  Compressing {i+1}/{total}: {rel_path}"
        print(progress_msg)

        if not args.overwrite and os.path.exists(final_ktx_path):
            print("    -> Skipping, already exists.")
            continue

        # 1. Determine source texture path (extracting from BSA if needed)
        source_texture_path = ""
        temp_source_to_clean = None
        if info['source_type'] == 'data_file':
            source_texture_path = info['full_path']
        else: # BSA file
            temp_source_path = os.path.join(temp_dir, os.path.basename(rel_path))
            if unpack_bsa_file(info['bsa_path'], rel_path, temp_source_path, args.verbose):
                source_texture_path = temp_source_path
                temp_source_to_clean = temp_source_path
            else:
                print(f"    -> Skipping, failed to extract from BSA.")
                continue
        
        # 2. Convert source to intermediate PNG
        temp_png_path = os.path.join(temp_dir, 'temp_conversion.png')
        try:
            run_command(['minimg', 'convert', source_texture_path, temp_png_path], args.verbose)
            
            # 3. Compress PNG to KTX using astcenc
            os.makedirs(os.path.dirname(final_ktx_path), exist_ok=True)
            # -cs for sRGB color textures (most common). Use -cn for normal maps, -cl for linear data if needed.
            astc_cmd = [
                'astcenc-neon', '-cs', temp_png_path, final_ktx_path,
                args.block_size, f'-{args.quality}'
            ]
            run_command(astc_cmd, args.verbose)
            print(f"    -> Saved to {final_ktx_path}")

        except Exception as e:
            print(f"    -> FAILED. An error occurred during processing: {e}", file=sys.stderr)
        finally:
            # 4. Clean up temporary files for this iteration
            if os.path.exists(temp_png_path):
                os.remove(temp_png_path)
            if temp_source_to_clean and os.path.exists(temp_source_to_clean):
                os.remove(temp_source_to_clean)

def main():
    parser = argparse.ArgumentParser(
        description=f"{MOD_NAME}: Discovers and compresses Morrowind textures to KTX/ASTC.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--openmw-cfg', required=True, help='Path to your openmw.cfg file.')
    parser.add_argument('--output-dir', required=True, help=f'Directory where the "{MOD_NAME}" mod folder will be created.')
    parser.add_argument('--mode', default='all', choices=['all', 'big_textures', 'small_textures'],
                        help='Filter which textures to process:\n'
                             '  all: Process all discovered textures (default).\n'
                             '  big_textures: Only textures with a dimension > --min-dim.\n'
                             '  small_textures: Only textures with dimensions <= --max-dim.')
    parser.add_argument('--min-dim', type=int, default=1024, help='Minimum dimension for "big_textures" mode (default: 1024).')
    parser.add_argument('--max-dim', type=int, default=512, help='Maximum dimension for "small_textures" mode (default: 512).')
    parser.add_argument('--block-size', default='6x6', help='ASTC block size (e.g., 4x4, 6x6, 8x8). Smaller is higher quality. (default: 6x6)')
    parser.add_argument('--quality', default='medium', choices=['veryfast', 'fast', 'medium', 'thorough', 'exhaustive'], help='ASTC encoding quality/speed preset (default: medium).')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing KTX files in the output directory.')
    parser.add_argument('--skip-discovery', action='store_true', help='Skip discovery and use an existing texture_db.json in the output directory.')
    parser.add_argument('--dry-run', action='store_true', help='Perform discovery and filtering, but do not compress any files.')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose output.')

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
    args = parser.parse_args()

    # --- Setup ---
    output_mod_root = os.path.join(args.output_dir, MOD_NAME)
    temp_dir = os.path.join(output_mod_root, TEMP_DIR_NAME)
    db_path = os.path.join(output_mod_root, DB_FILENAME)
    
    os.makedirs(output_mod_root, exist_ok=True)
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir) # Clean up from previous failed runs
    os.makedirs(temp_dir, exist_ok=True)

    print(f"--- {MOD_NAME} ---")
    print(f"Output directory: {output_mod_root}")
    
    final_texture_list = OrderedDict()

    # --- Discovery & Metadata ---
    if not args.skip_discovery:
        print("\n1. Parsing openmw.cfg...")
        data_paths, fallback_archives = parse_openmw_cfg(args.openmw_cfg)
        
        print("\n2. Discovering textures...")
        discovered = discover_files(data_paths, fallback_archives, args.verbose)
        print(f"   Found {len(discovered)} unique textures.")

        print("\n3. Populating metadata with 'minimg'...")
        populate_metadata(discovered, temp_dir, args.verbose)
        
        print("\n4. Filtering textures based on mode...")
        for rel_path, info in discovered.items():
            dims = info.get('dimensions')
            if not dims or None in dims:
                print(f"   Warning: Skipping '{rel_path}' from filters due to missing dimensions.")
                continue
            
            width, height = dims
            if args.mode == 'all': final_texture_list[rel_path] = info
            elif args.mode == 'big_textures' and (width > args.min_dim or height > args.min_dim): final_texture_list[rel_path] = info
            elif args.mode == 'small_textures' and (width <= args.max_dim and height <= args.max_dim): final_texture_list[rel_path] = info
        
        print(f"   Filtered list contains {len(final_texture_list)} textures.")
        
        print(f"\n5. Saving texture database to: {db_path}")
        with open(db_path, 'w', encoding='utf-8') as f:
            json.dump(final_texture_list, f, indent=4)
    else:
        print("\n--skip-discovery provided. Loading existing database...")
        try:
            with open(db_path, 'r', encoding='utf-8') as f:
                final_texture_list = json.load(f, object_pairs_hook=OrderedDict)
            print(f"   Loaded {len(final_texture_list)} textures from {db_path}.")
        except FileNotFoundError:
            print(f"Error: Database file not found at '{db_path}'. Cannot skip discovery.", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"Error: Could not parse database file '{db_path}'.", file=sys.stderr)
            sys.exit(1)

    # --- Compression ---
    if not args.dry_run:
        print(f"\n6. Starting compression...")
        compress_textures(final_texture_list, output_mod_root, temp_dir, args)
    else:
        print("\nDry Run enabled. Skipping compression.")

    # --- Cleanup ---
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    
    print("\n--- All Done! ---")
    if not args.dry_run:
        print("To use the compressed textures, add the following line to your openmw.cfg:")
        print(f'data="{output_mod_root}"')

if __name__ == '__main__':
    main()
