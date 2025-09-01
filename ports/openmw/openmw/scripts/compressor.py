#!/usr/bin/env python3
# Netch Leather Compressor - Stateful Texture Management & Compression
# Discovers, catalogs, and compresses Morrowind textures to KTX/ASTC format.

import argparse
import os
import subprocess
import json
import sys
import shutil
import hashlib
import time
import atexit
from collections import OrderedDict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Constants ---
MOD_NAME = "Netch Leather Compressor"
SUPPORTED_EXTENSIONS = ('.tga', '.bmp', '.dds')
DB_FILENAME = 'texture_db.json'
TEMP_DIR_NAME = '.temp'
BACKUP_DIR_NAME = 'backups'

# --- Utility Functions ---
# (Most utility functions are unchanged)

def normalize_path(path_str):
    base, _, _ = path_str.strip().replace('\\', '/').lower().rpartition('.')
    return base

def get_backup_name(path_str):
    return hashlib.sha256(path_str.encode('utf-8')).hexdigest() + ".ktx"

def run_command(cmd, verbose=False):
    if verbose: print(f"    Executing: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, capture_output=True, check=True, text=True, encoding='utf-8')
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{e.stderr.strip()}")
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: '{cmd[0]}'. Is it in your PATH?")

def unpack_bsa_file(bsa_path, file_in_bsa, dest_dir, verbose=False):
    if verbose: print(f"    Unpacking '{file_in_bsa}'...")
    try:
        run_command(['bsatool', 'extract', bsa_path, file_in_bsa, str(dest_dir)], verbose)
        return dest_dir / Path(file_in_bsa).name
    except Exception as e:
        print(f"\nWarning: Failed to unpack '{file_in_bsa}' from '{bsa_path}'. Error: {e}")
        return None

def get_image_info(file_path, verbose=False):
    try:
        json_output = subprocess.check_output(['minimg', 'info', str(file_path)], text=True)
        info = json.loads(json_output)
        return {"dimensions": [info.get('width', 0), info.get('height', 0)], "mipmaps": info.get('mipmaps', 1), "file_size": file_path.stat().st_size}
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, FileNotFoundError) as e:
        print(f"\nWarning: Could not parse metadata for '{file_path}'. Error: {e}")
        return None

# --- Progress Reporting ---
def esmm_progress(stage_name, step, total, info=""):
    """Prints progress in a machine-parseable format for ESMM."""
    # Escape quotes in info string for safe parsing
    print(f'INFO_STAGE_NAME={stage_name}')
    print(f'INFO_STAGE_INFO={safe_info}')
    print(f'INFO_STAGE_STEP={step}')
    print(f'INFO_STAGE_TOTAL={total}')
    sys.stdout.flush() # Ensure the C++ parent process sees the output immediately

# --- Database & State Management ---
_db_to_save_on_exit, _db_path_on_exit = None, None
def _save_db_on_crash():
    if _db_to_save_on_exit and _db_path_on_exit:
        print("\nCRITICAL: Script interrupted. Saving database state before exiting...", file=sys.stderr)
        save_database(_db_path_on_exit, _db_to_save_on_exit, is_crash=True)

def load_database(db_path):
    if db_path.exists():
        print(f"Loading existing database from {db_path}")
        with open(db_path, 'r', encoding='utf-8') as f: return json.load(f, object_pairs_hook=OrderedDict)
    return OrderedDict()

def save_database(db_path, database, is_crash=False):
    if not is_crash: print(f"Saving database to {db_path}")
    with open(db_path, 'w', encoding='utf-8') as f: json.dump(database, f, indent=4)

# --- Discovery & Reconciliation Logic ---
def parse_openmw_cfg(cfg_path):
    # ... (function is unchanged) ...
    data_paths, fallback_archives = [], []
    cfg_dir = cfg_path.parent
    with open(cfg_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(('#', ';')): continue
            key, _, value = line.partition('=')
            if value:
                path_str = value.strip().strip('"')
                if key.strip().lower() == 'data': data_paths.append(str((cfg_dir / path_str).resolve()))
                elif key.strip().lower() == 'fallback-archive': fallback_archives.append(path_str)
    return data_paths, fallback_archives

def discover_active_textures(data_paths, fallback_archives, verbose=False):
    # ... (function is unchanged) ...
    active_textures = OrderedDict()

    for data_root in reversed(data_paths):
        if not Path(data_root).is_dir(): continue
        if verbose: print(f"Scanning data directory: {data_root}")
        for p in Path(data_root).rglob('*'):
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                rel_path = str(p.relative_to(data_root))
                key = normalize_path(rel_path)
                if key not in active_textures: active_textures[key] = {"source_key": data_root, "full_path": str(p), "rel_path": rel_path, "source_type": "data_file", "bsa_path": None}

    for bsa_name in fallback_archives:
        for data_root in data_paths:
            bsa_path = Path(data_root) / bsa_name
            if bsa_path.is_file():
                if verbose: print(f"Scanning BSA archive: {bsa_path}")
                try:
                    file_list_raw = subprocess.check_output(['bsatool', 'list', str(bsa_path)], text=True)
                    for rel_path in file_list_raw.splitlines():
                        if Path(rel_path).suffix.lower() in SUPPORTED_EXTENSIONS:
                            key = normalize_path(rel_path)
                            if key not in active_textures: active_textures[key] = {"source_key": str(bsa_path), "full_path": rel_path, "rel_path": rel_path, "source_type": "bsa_file", "bsa_path": str(bsa_path)}
                except subprocess.CalledProcessError: pass
                break
    return active_textures


def reconcile_database(database, active_textures, temp_dir, args):
    if not args.esmm_progress:
        print("Reconciling database with current mod setup...")
    
    active_keys, db_keys = set(active_textures.keys()), set(database.keys())
    for key in (active_keys - db_keys):
        database[key] = {"current_source_key": None, "current_compression": None, "texture_sources": {}}

    total_to_process = len(active_textures)
    for i, (key, active_info) in enumerate(active_textures.items()):
        if args.esmm_progress:
            esmm_progress("Reconciling Database", i + 1, total_to_process, active_info['rel_path'])

        entry = database[key]
        entry["current_source_key"] = active_info["source_key"]
        if active_info["source_key"] not in entry["texture_sources"]:
            if not args.esmm_progress:
                print(f"  New source found for '{key}': {active_info['source_key']}")
            source_path, temp_file = Path(active_info["full_path"]), None
            if active_info["source_type"] == "bsa_file":
                temp_file = unpack_bsa_file(active_info["bsa_path"], active_info["full_path"], temp_dir, args.verbose)
                if temp_file: source_path = temp_file
            metadata = get_image_info(source_path, args.verbose) if source_path and source_path.exists() else None
            if temp_file: temp_file.unlink()
            if metadata: entry["texture_sources"][active_info["source_key"]] = {"backup_name": None, "backup_compression": None, **active_info, **metadata}
            
    for key in (db_keys - active_keys):
        entry = database[key]
        if entry["current_source_key"]:
            entry["_last_active_source"] = entry["current_source_key"]
            entry["current_source_key"] = None

# --- Parallel Processing Worker ---
def process_single_texture(key, entry, output_mod_root, temp_dir, backup_dir, args):
    # ... (function is unchanged internally) ...
    stats = {"status": "skipped", "original_size": 0, "compressed_size": 0}
    final_ktx_path = output_mod_root / (key + ".ktx")
    
    if not entry["current_source_key"]:
        if final_ktx_path.exists():
            old_source_key = entry.get("_last_active_source")
            if old_source_key:
                source_entry = entry["texture_sources"].get(old_source_key)
                if source_entry:
                    backup_name = get_backup_name(old_source_key)
                    shutil.move(str(final_ktx_path), str(backup_dir / backup_name))
                    source_entry["backup_name"] = backup_name
                    source_entry["backup_compression"] = entry["current_compression"]
                    entry["current_compression"] = None
                    stats["status"] = "deactivated"
        return key, stats

    current_source_key = entry["current_source_key"]
    active_source_info = entry["texture_sources"][current_source_key]
    stats["original_size"] = active_source_info.get("file_size", 0)
    compression_settings = {"block_size": args.block_size, "quality": args.quality}

    if final_ktx_path.exists() and entry["current_compression"] == compression_settings and not args.overwrite:
        return key, stats
    
    if active_source_info.get("backup_name") and active_source_info.get("backup_compression") == compression_settings:
        backup_path = backup_dir / active_source_info["backup_name"]
        if backup_path.exists():
            final_ktx_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup_path), str(final_ktx_path))
            active_source_info.update({"backup_name": None, "backup_compression": None})
            entry["current_compression"] = compression_settings
            entry["_last_active_source"] = current_source_key
            stats.update({"status": "restored", "compressed_size": final_ktx_path.stat().st_size})
            return key, stats
    
    if final_ktx_path.exists():
        old_source_key = entry.get("_last_active_source", current_source_key)
        if old_source_key in entry["texture_sources"]:
            backup_name = get_backup_name(old_source_key)
            shutil.move(str(final_ktx_path), str(backup_dir / backup_name))
            entry["texture_sources"][old_source_key]["backup_name"] = backup_name
            entry["texture_sources"][old_source_key]["backup_compression"] = entry["current_compression"]

    source_path, temp_source = Path(active_source_info["full_path"]), None
    if active_source_info["source_type"] == "bsa_file":
        temp_source = unpack_bsa_file(active_source_info["bsa_path"], active_source_info["full_path"], temp_dir, args.verbose)
        if not temp_source: return key, {"status": "failed", "original_size": stats["original_size"]}
        source_path = temp_source

    thread_temp_png = temp_dir / f"temp_{os.getpid()}_{key.replace('/', '_')}.png"
    try:
        run_command(['minimg', 'convert', str(source_path), str(thread_temp_png)])
        final_ktx_path.parent.mkdir(parents=True, exist_ok=True)
        kram_cmd = ['kram', 'encode', '-f', f'astc{args.block_size}', '-encoder', 'astcenc', '-quality', args.quality, '-flip', '-i', str(thread_temp_png), '-o', str(final_ktx_path)]
        run_command(kram_cmd, args.verbose)
        
        entry["current_compression"] = compression_settings
        entry["_last_active_source"] = current_source_key
        stats.update({"status": "compressed", "compressed_size": final_ktx_path.stat().st_size})
    except Exception as e:
        print(f"  -> FAILED processing {key}: {e}", file=sys.stderr)
        stats["status"] = "failed"
    finally:
        if thread_temp_png.exists(): thread_temp_png.unlink()
        if temp_source and temp_source.exists(): temp_source.unlink()
    
    return key, stats


def main():
    parser = argparse.ArgumentParser(description=f"{MOD_NAME}: Stateful texture compression for OpenMW.")
    parser.add_argument('--openmw-cfg', required=True, type=Path, help='Path to your openmw.cfg file.')
    parser.add_argument('--output-dir', required=True, type=Path, help=f'Directory for the "{MOD_NAME}" mod folder.')
    parser.add_argument('--block-size', default='6x6', help='ASTC block size (e.g., 4x4, 6x6). Default: 6x6')
    parser.add_argument('--quality', default='medium', help='ASTC encoding quality preset. Default: medium')
    parser.add_argument('--overwrite', action='store_true', help='Force re-compression even if a file exists with the same settings.')
    parser.add_argument('--dry-run', action='store_true', help='Perform discovery and reconciliation but do not modify any files.')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose output.')
    parser.add_argument('--esmm-progress', action='store_true', help='Output machine-parseable progress for GUI integration.')
    
    if len(sys.argv) == 1: parser.print_help(sys.stderr); sys.exit(1)
    args = parser.parse_args()

    stats = {"start_time": time.monotonic(), "discovery_time": 0, "compression_time": 0, "compressed": 0, "restored": 0, "skipped": 0, "deactivated": 0, "failed": 0, "total_original_size": 0, "total_compressed_size": 0}
    output_mod_root = args.output_dir / MOD_NAME
    temp_dir = output_mod_root / TEMP_DIR_NAME
    backup_dir = output_mod_root / BACKUP_DIR_NAME
    db_path = output_mod_root / DB_FILENAME
    
    output_mod_root.mkdir(exist_ok=True); backup_dir.mkdir(exist_ok=True)
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    temp_dir.mkdir(exist_ok=True)

    if not args.esmm_progress: print(f"--- {MOD_NAME} ---")
    database = load_database(db_path)
    
    discovery_start = time.monotonic()
    if not args.esmm_progress: print("\n1. Parsing openmw.cfg..."); 
    data_paths, fallback_archives = parse_openmw_cfg(args.openmw_cfg)
    if not args.esmm_progress: print("\n2. Discovering active textures..."); 
    active_textures = discover_active_textures(data_paths, fallback_archives, args.verbose)
    if not args.esmm_progress: print(f"   Found {len(active_textures)} unique active textures.")
    if not args.esmm_progress: print("\n3. Reconciling database..."); 
    reconcile_database(database, active_textures, temp_dir, args)
    stats["discovery_time"] = time.monotonic() - discovery_start

    if not args.dry_run:
        save_database(db_path, database)
        global _db_to_save_on_exit, _db_path_on_exit
        _db_to_save_on_exit, _db_path_on_exit = database, db_path
        atexit.register(_save_db_on_crash)

    if not args.dry_run:
        if not args.esmm_progress: print("\n4. Processing textures...")
        compression_start = time.monotonic()
        
        tasks_small, tasks_medium, tasks_large, tasks_other = [], [], [], []
        for key, entry in database.items():
            task = (key, entry)
            if entry.get("current_source_key"):
                dims = entry["texture_sources"][entry["current_source_key"]].get("dimensions", [0, 0])
                if dims[0] > 512 or dims[1] > 512: tasks_large.append(task)
                elif dims[0] > 64 or dims[1] > 64: tasks_medium.append(task)
                else: tasks_small.append(task)
            else: tasks_other.append(task)

        all_tasks = tasks_large + tasks_medium + tasks_small + tasks_other
        total_tasks_to_process = len(all_tasks)
        processed_count = 0

        # Create a single executor and submit all tasks
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(process_single_texture, k, e, output_mod_root, temp_dir, backup_dir, args): k for k, e in all_tasks}
            for future in as_completed(futures):
                processed_count += 1
                key, result_stats = future.result()

                if args.esmm_progress:
                    # Use the original relative path for display if available
                    info_key = key
                    entry = database.get(key)
                    if entry:
                        source_key = entry.get("current_source_key") or entry.get("_last_active_source")
                        if source_key and source_key in entry["texture_sources"]:
                            info_key = entry["texture_sources"][source_key].get("rel_path", key)
                    esmm_progress("Processing Textures", processed_count, total_tasks_to_process, info_key)
                
                stats[result_stats["status"]] += 1
                stats["total_original_size"] += result_stats["original_size"]
                stats["total_compressed_size"] += result_stats["compressed_size"]

        stats["compression_time"] = time.monotonic() - compression_start
        atexit.unregister(_save_db_on_crash)
        save_database(db_path, database)
    else:
        if not args.esmm_progress: print("\nDry Run enabled. Skipping file operations.")

    shutil.rmtree(temp_dir)
    stats["total_time"] = time.monotonic() - stats["start_time"]

    if not args.esmm_progress:
        print("\n--- Run Summary ---")
        # (Stats printing is unchanged)
        print(f"Total Time:             {stats['total_time']:.2f}s")
        print(f"  - Discovery Phase:    {stats['discovery_time']:.2f}s")
        print(f"  - Compression Phase:  {stats['compression_time']:.2f}s")
        print("-" * 25)
        print(f"Textures Processed:")
        print(f"  - Compressed:         {stats['compressed']}")
        print(f"  - Restored from backup: {stats['restored']}")
        print(f"  - Skipped (up-to-date): {stats['skipped']}")
        print(f"  - Deactivated:          {stats['deactivated']}")
        print(f"  - Failed:             {stats['failed']}")
        print("-" * 25)
        if stats['total_original_size'] > 0 and stats['total_compressed_size'] > 0:
            saved_bytes = stats['total_original_size'] - stats['total_compressed_size']
            reduction = (saved_bytes / stats['total_original_size']) * 100 if stats['total_original_size'] > 0 else 0
            print(f"Space Saved (for new/restored):")
            print(f"  - Original Size:      {stats['total_original_size'] / (1024*1024):.2f} MB")
            print(f"  - Compressed Size:    {stats['total_compressed_size'] / (1024*1024):.2f} MB")
            print(f"  - Space Saved:        {saved_bytes / (1024*1024):.2f} MB ({reduction:.1f}% reduction)")

        print("\n--- All Done! ---")


if __name__ == '__main__':
    main()
