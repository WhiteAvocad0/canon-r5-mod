#include "boot_policy.h"
#include "root_plan.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
CODE static int owned_root_range(uint32_t root)
{return !(root&16383u) && root>=0x100000u && root<=0x2fc000u;}
CODE unsigned root_primary_decision(unsigned result,uint32_t actual,uint32_t expected)
{
 if(result==ROOT_SWITCHED && owned_root_range(expected) && actual==expected)
  return ROOT_DECISION_MOD;
 /* These results precede the switch attempt. No ambiguous switch or local
  * restore is treated as authority to release either core into stock. */
 if(result>=ROOT_BAD_CPU && result<=ROOT_BAD_PLAN && actual==ROOT_ORIGINAL)
  return ROOT_DECISION_STOCK;
 return ROOT_DECISION_FATAL;
}
CODE unsigned root_secondary_action(unsigned decision,unsigned result,
 uint32_t actual,uint32_t expected)
{
 if(decision==ROOT_DECISION_PENDING)return ROOT_ENTRY_WAIT;
 if(decision==ROOT_DECISION_STOCK && actual==ROOT_ORIGINAL)return ROOT_ENTRY_STOCK;
 if(decision==ROOT_DECISION_MOD && result==ROOT_SWITCHED &&
  owned_root_range(expected) && actual==expected)return ROOT_ENTRY_MOD;
 return ROOT_ENTRY_HOLD;
}
