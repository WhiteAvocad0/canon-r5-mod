# Porting & Contribution Guide

This project currently supports **original Canon EOS R5 firmware `2.2.1` only**. Other EOS R5 firmware versions and other camera models (EOS R5 C, EOS R5 Mark II, EOS R6, EOS R6 Mark II, EOS R6 Mark III) are **unsupported** and require independent, target-specific ports.

> [!IMPORTANT]
> **Read-Only Recon First — Never Cross-Flash**
> Never invite users to test or cross-flash an EOS R5 `2.2.1` binary onto another camera model or firmware version, and never port addresses by guessing offsets from another ROM.

---

## 1. Required Target-Specific Evidence for a New Port

A valid port to a new firmware version or model (such as an R6-family camera) must provide independently verified, target-specific evidence for every layer below:

1. **ROM Provenance & Immutable Code Fingerprints**:
   - Acquired read-only ROM dump provenance (text digests / sanitized bounds only — never upload ROM or `.FIR` binaries).
   - Target-specific `FW_MAIN_START` additive word-sum guard (`FW_SIGNATURE` over `FW_SIGNATURE_WORDS`) and exact startup instruction bytes at every patch offset (`M['patches']`), allowing per-camera settings variance outside immutable code without bypassing code validation.
2. **Handoff, `UserPool`, Stack, and Cache Coherency**:
   - Verified `FW_AUTOEXEC_BASE` staging address, `FW_SECONDARY_SLOT` multi-core hold mailbox, and `FW_RESIDENT_BASE` / `FW_USER_START` / `FW_USER_LENGTH` / `FW_OBJECTS_START` / `FW_MINIMUM_USER_LEFT` pool bounds.
   - Verified `FW_CAMIF_FIXUP`, `FW_CAMIF_STATUS`, `FW_INIT_FACTORY`, `FW_CANON_INIT`, `FW_CACHE_CLEAN`, `FW_CACHE_INVALIDATE`, and `FW_BZERO` Thumb entry points and calling conventions.
   - Preserved Canon init-task stack for scheduling calls (`witness` / `audio_attempt` / `observer_start` / `sound_boot_start_worker`), limiting the private stack to early `CamIF` polling.
3. **Stock Audio Descriptors & Readiness Slots**:
   - Verified `SE_BeepStart` (`AUDIO_BEEP_START`), `AUDIO_STATE_QUERY`, `sleep_ms`, 64-byte descriptor layout (`OutSel`), 32-byte DMA alignment, and read-only readiness slots (`AUDIO_SINGLETON_SLOT`, `AudioCtrl`, `AudioIC`, output context, ICU cache kind/init, and `VAIF`/`AK` parameter slots).
4. **Release Counter & Positive-Delta Semantics**:
   - Verified `OBSERVER_COUNTER_CONTEXT_SLOT` + `counter_field_offset` from target disassembly.
   - Strict separation between **baseline initialization** and **capture advance**: initial reads, null/unobserved contexts, context-pointer changes, and counter resets (`val < baseline_val`) must **only** re-seed the baseline and must never trigger audio.
5. **Owned Raw Task IDs, Preemption Flags, and Cleanup**:
   - Verified non-asserting `create_task` / `start_task` / `delete_task` cores and failure sentinels (`raw_id == -1` failure check vs valid high-bit opaque `u32` handles).
   - Separate per-call `*_return_observed` flags so worker preemption inside `start_task` cannot be misread as an observed `-1` return.
   - Cleanup (`delete_task`) restricted strictly to failed `start_task` on a newly owned task; fault latching and non-blocking callback isolation (no audio submission, waiting, or FIO inside property-observer callbacks).
6. **State Cleanup & Busy Policy**:
   - Resetting shared audio observation state (`audio_state` words) on every playback attempt so startup state never leaks into shutter attempts.
   - Explicit busy/coalesced drop policy without queued delayed playback.

---

## 2. Contribution Boundaries

- **Do not upload** Canon ROM dumps, `.FIR` updates, private/copyrighted audio clips (such as Windows XP or Mambo), personal photos (`.CR3` / `.JPG`), or raw SD card images.
- **Allowed in issues and PRs**: Source changes, host unit tests (running without private lab paths or copyrighted assets), sanitized memory bounds, and text SHA-256 digests (`build.json` / `source_manifest_sha256` / ROM digest text).
- **No automatic binary releases**: Do not add CI workflows that build or publish `autoexec.bin` releases or embed Canon bytes.
- **No compatibility promises**: Filing a port request does not promise that a model or firmware version is compatible or will be supported.
