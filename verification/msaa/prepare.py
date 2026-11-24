"""Build synthetic GFX10 image-load programs, a deliberately wrong control, and the MSAA seed shaders."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

root, work = map(lambda arg: Path(arg).resolve(), sys.argv[1:])
sys.path.insert(0, str(root / "runtime/test/shaders"))
from compute_fixture import compute_header

for folder in ("disasm", "fssm", "spirv"):
    (work / folder).mkdir(parents=True, exist_ok=True)
variants = [(array, packed, False, False) for array in (False, True) for packed in (False, True)]
variants += [(False,True,True,False),(True,False,True,False),(True,False,False,True)]
for array, packed, d16, control in variants:
    name = ("cs_ms_" + ("array" if array else "2d") + ("_a16" if packed else "")
            + ("_d16" if d16 else "") + ("_zero" if control else ""))
    code = ["; group: 32,1,1", "; rsrc2: 0x20",
            "v_and_b32_e32 v1, 1, v0", "v_lshrrev_b32_e32 v2, 1, v0", "v_and_b32_e32 v2, 1, v2",
            "v_lshrrev_b32_e32 v6, 2, v0", "v_and_b32_e32 v6, 3, v6",
            "v_lshrrev_b32_e32 v7, 4, v0", "v_and_b32_e32 v7, 1, v7"]
    if control:
        code += ["v_mov_b32_e32 v6, 0"]
    if packed:
        code += ["v_lshlrev_b32_e32 v2, 16, v2", "v_or_b32_e32 v1, v1, v2"]
        if array:
            code += ["v_lshlrev_b32_e32 v6, 16, v6", "v_or_b32_e32 v2, v7, v6"]
        else:
            code += ["v_mov_b32_e32 v2, v6"]
        addr = "v[1:2]"
    else:
        code += ["v_mov_b32_e32 v3, " + ("v7" if array else "v6")]
        if array:
            code += ["v_mov_b32_e32 v4, v6"]
        addr = "v[1:4]" if array else "v[1:3]"
    dim = "SQ_RSRC_IMG_2D_MSAA" + ("_ARRAY" if array else "")
    if d16:
        code += ["v_mov_b32_e32 v9, 0xa55a0000", "v_mov_b32_e32 v10, 23", "v_mov_b32_e32 v11, 42"]
    dest, mask = ("v[8:9]", "0xb") if d16 else ("v[8:11]", "0xf")
    code += [f"image_load {dest}, {addr}, s[8:15] dmask:{mask} dim:{dim}" +
             (" a16" if packed else "") + (" d16" if d16 else ""),
             "s_waitcnt vmcnt(0)", "v_lshlrev_b32_e32 v12, 4, v0",
             "buffer_store_dwordx4 v[8:11], v12, s[0:3], 0 offen", "s_endpgm"]
    asm = work / (name + ".asm")
    asm.write_text("\n".join(code) + "\n", encoding="utf-8", newline="\n")
    result = subprocess.run([os.environ.get("LLVM_MC", "llvm-mc"), "-triple=amdgcn--amdpal", "-mcpu=gfx1030",
                             "-show-encoding"], input=asm.read_text(), text=True, capture_output=True, check=True)
    lines = [ln for ln in result.stdout.splitlines() if not ln.lstrip().startswith(".")]
    (work / "disasm" / (name + ".s")).write_text("; " + name + " hand-written test program\n" + "\n".join(lines) + "\n",
                                                  encoding="utf-8", newline="\n")
    (work / "fssm" / (name + ".fssm")).write_bytes(compute_header(asm))
    (work / (name + ".dxbc")).write_bytes(b"DXBC" + bytes(28))
subprocess.run([sys.executable, "-I", root / "tools/rdna2spirv.py", work / "disasm", "--out", work / "spirv",
                "--msaa-image-load", "--no-float-controls"], check=True)
assert all((work / "spirv" / (item.stem + ".spv")).exists() for item in (work / "disasm").glob("*.s"))
glslang = shutil.which("glslangValidator") or shutil.which("glslang")
subprocess.run([glslang, "-V", root / "runtime/test/glsl/msaa_seed.vert", "-o", work / "seed.vert.spv"], check=True)
frag = work / "seed.frag"
frag.write_text("""#version 450
layout(location=0) flat in int tag;
layout(location=0) out uvec4 color;
void main() {
    gl_SampleMask[0] = 1 << (tag % 4);
    uint base = 0xbf800000u + uint(tag) * 4096u + uint(gl_FragCoord.y) * 256u + uint(gl_FragCoord.x) * 16u;
    color = uvec4(base) + uvec4(1,2,3,4);
}
""", encoding="utf-8", newline="\n")
subprocess.run([glslang, "-V", frag, "-o", work / "seed.frag.spv"], check=True)
