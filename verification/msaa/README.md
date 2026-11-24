# Ordinary GFX10 multisample texture reads on Windows

These are synthetic readbacks for `SHADER-IMAGE-LOAD-MSAA`. The two scripts run against a checkout of the proposed translator. Its `tools/rdna2spirv.py` Git blob is `a9cdfb1289e609ad418f37dfd8db0bc9ce3aeef3`. The upstream baseline was `66d4f5aa36bb6bcd006fa4b311c7a84d809fdbad`.

| Selected adapter | Driver | Positive dispatches | Four-word results matched | Deliberate wrong-sample control | Vulkan validation errors |
|---|---|---:|---:|---|---:|
| NVIDIA GeForce RTX 3070 eGPU, PCI 10de:2484 | 610.88 (32.0.16.1088) | 18/18 | 576/576 | All 72 expected mismatches detected | 0 |
| Intel(R) Arc(TM) Graphics, PCI 8086:7d55 | 32.0.101.8132 | 18/18 | 576/576 | All 72 expected mismatches detected | 0 |

Both runs used Windows 11 Pro 26H2 build 26300.9974, Python 3.12.14, LLVM 23.1.6, SPIRV-Tools 2026.4, glslang 16.6.0 and Khronos validation layers 1.4.357. No hardware cases were skipped. The exact device name is matched when enumerating Vulkan physical devices. The first line in each log records the selected device and its PCI IDs. [NVIDIA output](nvidia.txt) · [Intel output](intel.txt)

`prepare.py` generates seven small GFX10 programs using LLVM's gfx1030 assembler. It feeds their listings and synthetic headers into the translator's command-line entry point. All seven passed `spirv-val`. The test rasterizes independent values into four samples of two layers, using a distinct four-channel texture for each numeric class. A compute shader reads the texture into a mapped buffer. The CPU compares every output word, including the untouched registers and upper half of the last packed D16 destination. Six programs cover both dimensions, packed and unpacked addresses and sparse D16 results. The seventh always reads sample zero. Of 32 lanes per numeric class in that control, the eight lanes that requested sample zero should still match.

The D16 float expectation uses the repository's GFX10 rounding contract for RGBA32_FLOAT. An initial run exposed an incorrect round-to-nearest assumption in this probe. Both adapters passed after the independent expectation used round-toward-zero. This measures the translated shader on Vulkan. Native RDNA2 hardware and game content were outside this run.

With `llvm-mc`, `spirv-as`, `spirv-val`, `glslangValidator` and the Khronos validation layer available, run the following in a Python environment containing the public PyPI package `vulkan==1.3.275.1`. The first path is the checkout with the code change. The second is a fresh working directory.

```powershell
python -I prepare.py C:\work\leonida C:\work\msaa-generated
python -I readback.py C:\work\msaa-generated "NVIDIA GeForce RTX 3070" --validation
python -I readback.py C:\work\msaa-generated "Intel(R) Arc(TM) Graphics" --validation
```

The graphics seed uses a four-sample RGBA32_UINT render target with compatible float, unsigned and signed sampled views. The host needs a graphics and compute queue, buffer device addresses, runtime descriptor arrays, sampled-image array dynamic indexing, 64-bit integers and 144 bytes of push constants. The output logs were recorded on 24 November 2026. Codex-assisted.
