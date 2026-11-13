ENTRY(_start)
SECTIONS {
 . = @AUTOEXEC_BASE@;
 .entry : { KEEP(*(.entry)) }
 _resident_load = ALIGN(LOADADDR(.entry)+SIZEOF(.entry),16);
 .resident @RESIDENT_BASE@ : AT(_resident_load) { _resident_start = .; *(.resident) *(.resident_text) *(.resident_rodata) *(.resident_data) . = ALIGN(16); _resident_data_end = .; }
 .bss (NOLOAD) : { _bss_start = .; *(.bss .bss.*) . = ALIGN(16); . += @STACK_BYTES@; _private_stack_top = .; _reserved_end = .; }
 .footer (_resident_load+SIZEOF(.resident)) : AT(_resident_load+SIZEOF(.resident)) { KEEP(*(.footer)) }
 _resident_load_offset = _resident_load - @AUTOEXEC_BASE@;
 _image_bytes = LOADADDR(.footer)+SIZEOF(.footer)-@AUTOEXEC_BASE@;
 /DISCARD/ : { *(.comment) *(.note*) }
 ASSERT((_reserved_end & 15)==0, "reservation alignment")
 ASSERT(_reserved_end < @OBJECTS_START@, "object pool overlap")
}
