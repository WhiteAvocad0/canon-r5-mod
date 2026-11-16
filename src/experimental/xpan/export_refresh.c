#include "export_refresh.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
CODE unsigned export_refresh_context_valid(const struct export_refresh_context *c)
{
 unsigned mode=c->cpsr&31u;
 return (mode==0x13u || mode==0x1fu) && !(c->cpsr&0xc0u) &&
        c->cpu==0 && c->route_cpu==0 && c->native100c==0 && c->native102c==0 && c->task!=0;
}
CODE void export_refresh_request_ordinary(struct export_refresh_state *s)
{ __atomic_store_n(&s->pending,1u,__ATOMIC_RELEASE); }
CODE unsigned export_refresh_begin(struct export_refresh_state *s,unsigned *ticket)
{
 unsigned expected=0;
 if(!__atomic_compare_exchange_n(&s->waiter,&expected,1u,1,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED))
  return EXPORT_REFRESH_BUSY;
 unsigned previous=__atomic_load_n(&s->request,__ATOMIC_RELAXED);
 if(previous==UINT32_MAX) { export_refresh_release(s);return EXPORT_REFRESH_TICKET_EXHAUSTED; }
 *ticket=previous+1u;
 __atomic_store_n(&s->request,*ticket,__ATOMIC_RELEASE);
 return EXPORT_REFRESH_PENDING;
}
CODE void export_refresh_release(struct export_refresh_state *s)
{ __atomic_store_n(&s->waiter,0u,__ATOMIC_RELEASE); }
CODE unsigned export_refresh_ack(struct export_refresh_state *s,unsigned ticket,unsigned *result,unsigned *report)
{
 if(!ticket || __atomic_load_n(&s->ack,__ATOMIC_ACQUIRE)!=ticket) return 0;
 *result=s->ack_result;*report=s->ack_report;return 1;
}
CODE void export_refresh_worker(struct export_refresh_state *s,const struct export_refresh_context *c,
 export_refresh_emit_fn emit,export_refresh_marker_fn marker,void *opaque)
{
 unsigned normal=export_refresh_context_valid(c);
 __atomic_store_n(&s->worker_context,normal?c->task:0u,__ATOMIC_RELEASE);
 if(s->polls<50u && ++s->polls==50u) export_refresh_request_ordinary(s);
 unsigned ticket=__atomic_load_n(&s->request,__ATOMIC_ACQUIRE);
 unsigned forced=ticket!=0 && ticket>s->handled;
 if(!forced && (s->polls<50u || s->reports>=EXPORT_REFRESH_ORDINARY_MAX || !emit ||
    !__atomic_load_n(&s->pending,__ATOMIC_ACQUIRE))) return;
 unsigned result=EXPORT_REFRESH_OK,report=0;
 if(!emit || (forced && (!normal || !marker))) result=EXPORT_REFRESH_UNSUPPORTED;
 else if(s->reports>=EXPORT_REFRESH_FORCED_MAX) result=EXPORT_REFRESH_EXHAUSTED;
 else {
  __atomic_exchange_n(&s->pending,0u,__ATOMIC_ACQ_REL);
  report=++s->reports;
  emit(opaque,report);
 }
 if(forced) {
  /* Captured ticket is never re-read to label this completed callback. */
  if(normal && marker) marker(opaque,ticket,report,result);
  s->handled=ticket;
  s->ack_result=result;s->ack_report=report;
  __atomic_store_n(&s->ack,ticket,__ATOMIC_RELEASE);
 }
}
CODE static unsigned admitted(struct export_refresh_state *s,const struct export_refresh_io *io)
{
 struct export_refresh_context c=io->context(io->opaque);
 unsigned worker=__atomic_load_n(&s->worker_context,__ATOMIC_ACQUIRE);
 return export_refresh_context_valid(&c) && worker!=0 && worker!=c.task;
}
CODE unsigned export_refresh_wait(struct export_refresh_state *s,uint32_t caller,uint32_t source,
 uint32_t length,const struct export_refresh_io *io)
{
 if(caller!=0xe02a5915u || source!=0x02140000u || length!=0x40000u) return EXPORT_REFRESH_SCOPE;
 if(__atomic_load_n(&s->waiter,__ATOMIC_ACQUIRE)) return EXPORT_REFRESH_BUSY;
 if(!io || !io->context || !io->sleep || !admitted(s,io)) return EXPORT_REFRESH_UNSUPPORTED;
 unsigned ticket=0,result=export_refresh_begin(s,&ticket),report=0;
 if(result!=EXPORT_REFRESH_PENDING) return result;
 result=EXPORT_REFRESH_TIMEOUT;
 for(unsigned i=0;i<=EXPORT_REFRESH_POLLS;i++) {
  if(export_refresh_ack(s,ticket,&result,&report)) break;
  if(i==EXPORT_REFRESH_POLLS) break;
  if(!admitted(s,io)) { result=EXPORT_REFRESH_UNSUPPORTED;break; }
  io->sleep(io->opaque,EXPORT_REFRESH_SLEEP_MS);
 }
 export_refresh_release(s);
 return result;
}
