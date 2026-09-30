# Linux CUDA detection and repair

Faster Whisper requires NVIDIA cuBLAS for CUDA 12 and cuDNN 9. An NVIDIA driver
and a visible GPU alone do not prove these libraries are available. In particular,
`Library libcublas.so.12 is not found or cannot be loaded` can occur with a short
WAV or MP4; it does not establish that the recording is too long or corrupted.

## Automatic preparation

Meet2Notes checks the driver and native libraries in a separate process with a
timeout. This isolates native crashes from the web server. It searches the active
Python environment's NVIDIA packages, PyTorch libraries, the conda/Pinokio `lib`
directory, existing absolute `LD_LIBRARY_PATH` entries, `CUDA_HOME` / `CUDA_PATH`,
and standard `/usr/local/cuda*` and `/opt/cuda` library directories. The system
linker's existing configuration is respected first.

Compatible libraries are preloaded into the application in dependency order,
including cuDNN components loaded lazily during inference. This works with pip
packages outside the system linker path without editing shell profiles or
`/etc/ld.so.conf`. Major versions are matched explicitly; cuDNN 8 and CUDA 13
libraries cannot replace cuDNN 9 and CUDA 12.

The CUDA Linux installer and Pinokio installation/update check for missing
libraries and install them into their private Python environment. Normal app
startup does not download packages. Existing compatible packages are retained.
If package metadata exists but required library files are damaged or missing,
repair reinstalls only the affected NVIDIA wheels at their exact installed versions.
No driver installation, sudo command, or system Python modification is attempted.

## When CUDA is unavailable

- **Auto:** Faster Whisper uses CPU/int8 and reports the diagnostic in the activity
  log and **Settings → Transcription**. Saved GPU preferences are not rewritten.
- **CPU:** continues to use CPU.
- **CUDA selected explicitly:** fails before attempting model inference and gives
  the diagnostic and repair command. Choose Auto to continue on CPU.

When libraries are missing, Settings offers **Install missing CUDA libraries**
if Meet2Notes is running in a virtual environment or conda environment. This
downloads NVIDIA packages (potentially more than 1 GB), verifies them, and asks
for an application restart. The running process keeps using its original runtime
until restarted. A failed repair reports an error rather than claiming success.

For manual diagnosis, use the same Python that launches Meet2Notes:

```bash
.venv/bin/python -m local_meeting_ai.infrastructure.linux_cuda
.venv/bin/python -m local_meeting_ai.infrastructure.linux_cuda --install
```

For Pinokio, run the command shown in Settings in its application terminal; that
command uses the exact active Python executable. Restart Meet2Notes afterwards.

If a driver check fails, run `nvidia-smi`. Install or repair the NVIDIA driver
using your distribution's instructions (including Arch/Omarchy); Python packages
cannot repair a kernel driver. Docker additionally needs GPU passthrough, normally
`--gpus all`, and a working NVIDIA container runtime on the host. A CUDA status
or allocation error can also indicate unavailable GPU resources. Use CPU/Auto
while resolving it. CUDA does not provide acceleration for AMD or Intel GPUs.

Diagnostics are cached for the application process so routine status polling
does not repeatedly initialize CUDA. Restart after changing drivers or libraries.
Other engines using PyTorch have their own runtime requirements; this repair does
not replace PyTorch or install another transcription model.

## Validation

Tested in Linux Docker under WSL2 with an RTX 3070, Python 3.11, Faster Whisper
1.2.1, CTranslate2 4.8.1 and the small model. A 12-second demo reproduces the
original missing-cuBLAS failure. With installed NVIDIA pip libraries but no
`LD_LIBRARY_PATH`, automatic preparation makes the actual Meet2Notes engine
transcribe on GPU. This is not a validation of every Linux distribution, GPU,
or three-hour workload.

Upstream requirements: [Faster Whisper GPU documentation](https://github.com/SYSTRAN/faster-whisper#gpu).
