#ifndef R5_MENU_NATIVE_H
#define R5_MENU_NATIVE_H
#include <stdint.h>
enum menu_prepare_result { PREP_NONE,PREP_BAD_CORE,PREP_BAD_HEAP,PREP_ALLOC_FAILED,
 PREP_BAD_RANGE,PREP_BAD_TABLE,PREP_READY,PREP_BAD_MAPPING };
enum menu_install_result { INSTALL_NONE,INSTALL_UNPREPARED,INSTALL_BAD_ROOT,INSTALL_BAD_GETTER,
 INSTALL_BAD_CALLBACK,INSTALL_BAD_READBACK,INSTALL_OK };
void menu_prepare(void);
void menu_install(uint32_t original_result);
#include "boot_mmu.h"
void menu_report(unsigned sequence,const volatile struct boot_mmu_snapshot *current);
void menu_heap_shim(void),menu_resource_shim(void);
#endif
