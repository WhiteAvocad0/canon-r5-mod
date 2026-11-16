#ifndef R5_BOOT_POLICY_H
#define R5_BOOT_POLICY_H
#include <stdint.h>
enum root_decision {ROOT_DECISION_PENDING,ROOT_DECISION_MOD,
 ROOT_DECISION_STOCK,ROOT_DECISION_FATAL};
enum root_entry_action {ROOT_ENTRY_WAIT,ROOT_ENTRY_STOCK,ROOT_ENTRY_MOD,
 ROOT_ENTRY_HOLD};
unsigned root_primary_decision(unsigned result,uint32_t actual_root,uint32_t expected_root);
unsigned root_secondary_action(unsigned decision,unsigned result,
 uint32_t actual_root,uint32_t expected_root);
#endif
