"""Canon boot-marker preparation for a camera-formatted SD card (issue #16 Phase 2).

Standard library only, no GUI imports:

- layout.py   parses the MBR and the FAT32 or exFAT boot area, and plans the
              marker writes and restores (pure functions on bytes);
- helper.py   the small privileged raw-sector helper, run through the OS
              password prompt with an argument whitelist;
- devices.py  finds removable SD cards and runs diskutil and the helper (macOS);
              Windows and Linux enumeration is best-effort and untested;
- workflow.py read, back up, write and verify, and restore.

It never formats, partitions or erases a card, never writes the MBR, and never
touches the camera's BootDisk flag or NVRAM.
"""
