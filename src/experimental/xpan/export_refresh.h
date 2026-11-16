#ifndef R5_EXPORT_REFRESH_H
#define R5_EXPORT_REFRESH_H
#include <stdint.h>
#define EXPORT_REFRESH_POLLS 25u
#define EXPORT_REFRESH_SLEEP_MS 20u
#define EXPORT_REFRESH_ORDINARY_MAX 7u
#define EXPORT_REFRESH_FORCED_MAX 8u
enum export_refresh_result { EXPORT_REFRESH_PENDING=0, EXPORT_REFRESH_OK=1,
 EXPORT_REFRESH_EXHAUSTED=2, EXPORT_REFRESH_UNSUPPORTED=3, EXPORT_REFRESH_BUSY=4,
 EXPORT_REFRESH_TICKET_EXHAUSTED=5, EXPORT_REFRESH_TIMEOUT=6, EXPORT_REFRESH_SCOPE=7 };
/* Zero-initialized once at boot; never reset live. Plain words are worker-owned
 * or protected by waiter + release/acquire ACK. Not a coherent sampled tuple. */
struct export_refresh_state {
 uint32_t waiter,request,ack,ack_result,ack_report,worker_context;
 uint32_t handled,reports,pending,polls;
};
/* cpu is MPIDR affinity0; route_cpu is GetCore's source-owned kernel1008.
 * They are separate observations. Unknown route is UINT32_MAX, never zero. */
struct export_refresh_context { uint32_t cpsr,cpu,native100c,native102c,task,route_cpu; };
typedef void (*export_refresh_emit_fn)(void *,unsigned);
typedef void (*export_refresh_marker_fn)(void *,unsigned,unsigned,unsigned);
struct export_refresh_io {
 struct export_refresh_context (*context)(void *);
 void (*sleep)(void *,unsigned);
 void *opaque;
};
unsigned export_refresh_context_valid(const struct export_refresh_context *);
void export_refresh_request_ordinary(struct export_refresh_state *);
unsigned export_refresh_begin(struct export_refresh_state *,unsigned *);
void export_refresh_release(struct export_refresh_state *);
unsigned export_refresh_ack(struct export_refresh_state *,unsigned,unsigned *,unsigned *);
void export_refresh_worker(struct export_refresh_state *,const struct export_refresh_context *,export_refresh_emit_fn,export_refresh_marker_fn,void *);
unsigned export_refresh_wait(struct export_refresh_state *,uint32_t,uint32_t,uint32_t,const struct export_refresh_io *);
extern struct export_refresh_state export_refresh_control;
struct export_refresh_context export_refresh_native_context(void *);
void export_refresh_native_before_copy(uint32_t,uint32_t,uint32_t);
/* Address binding only; the inline entry receives Canon's original registers. */
void export_refresh_precopy(void);
#endif
