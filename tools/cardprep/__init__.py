"""Canon boot-marker preparation for a camera-formatted SD card (issue #16 Phase 2).

Standard library only, no GUI imports:

- layout.py   parses the MBR and the FAT32 or exFAT boot area, and plans the
              marker writes and restores (pure functions on bytes);
- rawio.py    opens the card's whole-disk device through macOS authopen (the
              system password window; nothing runs as root) and does the exact,
              whitelisted reads and writes on the descriptor it receives;
- devices.py  finds removable SD cards and runs diskutil (macOS);
              Windows and Linux enumeration is best-effort and untested;
- workflow.py read, back up, write and verify, and restore.

It never formats, partitions or erases a card, never writes the MBR, and never
touches the camera's BootDisk flag or NVRAM.
"""
