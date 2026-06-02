# push local file to remote MobileAnJian
import io
import os
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

root_path = Path(__file__).parent

# mobile andanjian path
commandLib_path = "/sdcard/MobileAnJian/commandLib/"
plugin_path = "/sdcard/MobileAnJian/Plugin/"
script_path = "/sdcard/MobileAnJian/Script/"

def _adb_push(local: Path, remote: str) -> bool:
    cmd = ["adb", "push", str(local.resolve()), remote]
    result = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode == 0


def _push_one_file(local_file: Path, target_dir: str, remote_name: str | None = None) -> int:
    name = remote_name or local_file.name
    remote = f"{target_dir.rstrip('/')}/{name}"
    if _adb_push(local_file, remote):
        print(f"Pushed {local_file.name} -> {remote}")
        return 1
    print(f"Failed to push {local_file.name} -> {remote}")
    return 0


def _push_dir_flat(local_root: Path, remote_root: str) -> int:
    """将目录下所有文件扁平推到 remote_root（仅用文件名，不含 zip 内子路径）。"""
    pushed = 0
    base = remote_root.rstrip("/")
    for f in sorted(local_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(local_root).as_posix()
        remote = f"{base}/{f.name}"
        if _adb_push(f, remote):
            print(f"Pushed {rel} -> {remote}")
            pushed += 1
        else:
            print(f"Failed to push {rel} -> {remote}")
    return pushed


def push(source_path):
    src = Path(source_path)
    if not src.is_dir():
        print(f"Source path {source_path} does not exist or is not a directory")
        return

    pushed = 0
    for file in src.iterdir():
        if not file.is_file():
            continue
        name = file.name
        if name.endswith(".mql"):
            pushed += _push_one_file(file, commandLib_path)
        elif name.endswith((".lua", ".luae", ".info", ".html")):
            pushed += _push_one_file(file, plugin_path)
        elif name.endswith(".mqb"):
            temp_folder = decompress_mqb(file)
            if temp_folder is None:
                continue
            try:
                pushed += _push_dir_flat(temp_folder, script_path)
            finally:
                clean_temp_folder(temp_folder)
        else:
            print(f"Unsupported file type: {name}")

    print(f"Done. {pushed} file(s) pushed from {src}")

def _patch_zip_clear_utf_filename_bits(data: bytearray) -> bool:
    """清除 ZIP 中央目录与本地头里的 EFS UTF-8 文件名位（bit 11）。

    若该位被置位，CPython 的 zipfile 会强制按 UTF-8 解码文件名，忽略
    metadata_encoding。部分国内工具在 GBK 文件名上误设此位，7-Zip 仍能打开。
    """
    z = zipfile
    endrec = z._EndRecData(io.BytesIO(data))
    if not endrec:
        return False
    size_cd = endrec[z._ECD_SIZE]
    offset_cd = endrec[z._ECD_OFFSET]
    concat = endrec[z._ECD_LOCATION] - size_cd - offset_cd
    if endrec[z._ECD_SIGNATURE] == z.stringEndArchive64:
        concat -= z.sizeEndCentDir64 + z.sizeEndCentDir64Locator
    start_dir = offset_cd + concat
    end = start_dir + size_cd
    if start_dir < 0 or end > len(data):
        return False

    patched = False
    pos = start_dir
    while pos < end:
        if pos + z.sizeCentralDir > len(data):
            return False
        chunk = data[pos : pos + z.sizeCentralDir]
        if chunk[0:4] != z.stringCentralDir:
            return False
        centdir = struct.unpack(z.structCentralDir, chunk)
        flags = centdir[z._CD_FLAG_BITS]
        fn_len = centdir[z._CD_FILENAME_LENGTH]
        ex_len = centdir[z._CD_EXTRA_FIELD_LENGTH]
        cm_len = centdir[z._CD_COMMENT_LENGTH]
        local_off = centdir[z._CD_LOCAL_HEADER_OFFSET] + concat

        if flags & z._MASK_UTF_FILENAME:
            lst = list(centdir)
            lst[z._CD_FLAG_BITS] = flags & ~z._MASK_UTF_FILENAME
            data[pos : pos + z.sizeCentralDir] = struct.pack(z.structCentralDir, *lst)
            patched = True

        if 0 <= local_off and local_off + z.sizeFileHeader <= len(data):
            lh = data[local_off : local_off + z.sizeFileHeader]
            if lh[0:4] == z.stringFileHeader:
                fh = struct.unpack(z.structFileHeader, lh)
                fflags = fh[z._FH_GENERAL_PURPOSE_FLAG_BITS]
                if fflags & z._MASK_UTF_FILENAME:
                    flst = list(fh)
                    flst[z._FH_GENERAL_PURPOSE_FLAG_BITS] = fflags & ~z._MASK_UTF_FILENAME
                    data[local_off : local_off + z.sizeFileHeader] = struct.pack(
                        z.structFileHeader, *flst
                    )
                    patched = True

        step = z.sizeCentralDir + fn_len + ex_len + cm_len
        if step <= 0 or pos + step > end:
            return False
        pos += step

    return patched


def _zip_try_extract(src_maker, extract_dir: Path) -> bool:
    for enc in ("gbk", "gb18030", "utf-8", "cp437"):
        try:
            with zipfile.ZipFile(src_maker(), "r", metadata_encoding=enc) as zf:
                zf.extractall(extract_dir)
            return True
        except UnicodeDecodeError:
            continue
        except zipfile.BadZipFile:
            return False
    try:
        with zipfile.ZipFile(src_maker(), "r") as zf:
            zf.extractall(extract_dir)
        return True
    except Exception:
        return False


def _try_extract_7z(archive: Path, outdir: Path) -> bool:
    exes: list[str] = []
    for name in ("7z", "7za"):
        p = shutil.which(name)
        if p:
            exes.append(p)
    if os.name == "nt":
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_key)
            if not base:
                continue
            cand = Path(base) / "7-Zip" / "7z.exe"
            if cand.is_file() and str(cand) not in exes:
                exes.append(str(cand))
    outdir.mkdir(parents=True, exist_ok=True)
    ar = str(archive.resolve())
    od = str(outdir.resolve())
    for exe in exes:
        r = subprocess.run(
            [exe, "x", "-y", f"-o{od}", ar],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0 and any(outdir.iterdir()):
            return True
    return False


# obviously, mqb is a zip file, just decompress it and push to remote MobileAnJian path
def decompress_mqb(file_path):
    if not str(file_path).endswith(".mqb"):
        return None
    path = Path(file_path)
    extract_dir = path.parent / path.stem
    extract_dir.mkdir(parents=True, exist_ok=True)

    raw = path.read_bytes()
    patched = bytearray(raw)
    patched_ok = _patch_zip_clear_utf_filename_bits(patched)

    def src_makers():
        if patched_ok:
            blob = bytes(patched)
            yield lambda b=blob: io.BytesIO(b)
        yield lambda: path

    for maker in src_makers():
        if _zip_try_extract(maker, extract_dir):
            return extract_dir

    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    if _try_extract_7z(path, extract_dir):
        return extract_dir

    print(f"Failed to decompress {path.name} (zipfile + 7z)")
    shutil.rmtree(extract_dir, ignore_errors=True)
    return None

def clean_temp_folder(temp_folder):
    if temp_folder.exists():
        shutil.rmtree(temp_folder)

# for local test only 尚未集成进项目
if __name__ == "__main__":
    push(Path("res"))