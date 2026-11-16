/* Host-only typed metadata export; actual q_label.c owns values/policy. */
#include <stdio.h>
#include <stddef.h>
#include <stdint.h>
#include "q_label.h"
static unsigned comma;
static void number(const char*n,size_t v){printf("%s\"%s\":%zu",comma++?",":"",n,v);}
static void bytes(const void*p,size_t n){const unsigned char*v=p;for(size_t i=0;i<n;i++)printf("%02x",v[i]);}
static void field(const char*n,size_t at,size_t nbytes){printf("%s\"%s\":{\"word\":%zu,\"count\":%zu}",comma++?",":"",n,at/4u,nbytes/4u);}
#define FIELD(type,member) field(#member,offsetof(struct type,member),sizeof(((struct type*)0)->member))
int main(void){
 const uint16_t endian=1;if(*(const unsigned char*)&endian!=1)return 2;
 printf("{\"schema\":\"canon-r5-lab.q-label-c-model.v1\",\"profile\":{\"bytes\":%zu,\"hex\":\"",sizeof q_label_profile);bytes(&q_label_profile,sizeof q_label_profile);
 printf("\",\"values\":{");comma=0;
 number("schema",q_label_profile.schema);
 number("id",q_label_profile.id);
 number("english_pointer",q_label_profile.english_pointer);
 number("text_bytes",q_label_profile.text_bytes);
 number("property_caller",q_label_profile.property_caller);
 number("property_outer",q_label_profile.property_outer);
 number("property_frame",q_label_profile.property_frame);
 number("property_return_offset",q_label_profile.property_return_offset);
 number("property_kind_offset",q_label_profile.property_kind_offset);
 number("property_kind",q_label_profile.property_kind);
 number("property_id_offset",q_label_profile.property_id_offset);
 number("property_item_offset",q_label_profile.property_item_offset);
 number("property_item",q_label_profile.property_item);
 number("width_caller",q_label_profile.width_caller);
 number("width_outer",q_label_profile.width_outer);
 number("width_frame",q_label_profile.width_frame);
 number("width_return_offset",q_label_profile.width_return_offset);
 number("width_id_offset",q_label_profile.width_id_offset);
 number("item_key",q_label_profile.item_key);
 printf("}},\"text\":{\"bytes\":%zu,\"hex\":\"",sizeof q_label_text);bytes(q_label_text,sizeof q_label_text);
 printf("\"},\"layouts\":{");comma=0;number("cpu_bytes",sizeof(struct q_label_cpu));number("facts_bytes",sizeof(struct q_label_facts));
 printf("},\"fields\":{\"cpu\":{");comma=0;FIELD(q_label_cpu,r);FIELD(q_label_cpu,lr);
 printf("},\"facts\":{");comma=0;
 FIELD(q_label_facts,id);
 FIELD(q_label_facts,pointer);
 FIELD(q_label_facts,entry_lr);
 FIELD(q_label_facts,native_sp);
 FIELD(q_label_facts,outer_lr);
 FIELD(q_label_facts,property);
 FIELD(q_label_facts,saved_id);
 FIELD(q_label_facts,item);
 FIELD(q_label_facts,item_key);
 printf("},\"profile\":{");comma=0;
 FIELD(q_label_binding_profile,schema);
 FIELD(q_label_binding_profile,id);
 FIELD(q_label_binding_profile,english_pointer);
 FIELD(q_label_binding_profile,text_bytes);
 FIELD(q_label_binding_profile,property_caller);
 FIELD(q_label_binding_profile,property_outer);
 FIELD(q_label_binding_profile,property_frame);
 FIELD(q_label_binding_profile,property_return_offset);
 FIELD(q_label_binding_profile,property_kind_offset);
 FIELD(q_label_binding_profile,property_kind);
 FIELD(q_label_binding_profile,property_id_offset);
 FIELD(q_label_binding_profile,property_item_offset);
 FIELD(q_label_binding_profile,property_item);
 FIELD(q_label_binding_profile,width_caller);
 FIELD(q_label_binding_profile,width_outer);
 FIELD(q_label_binding_profile,width_frame);
 FIELD(q_label_binding_profile,width_return_offset);
 FIELD(q_label_binding_profile,width_id_offset);
 FIELD(q_label_binding_profile,item_key);
 printf("}}}\n");return ferror(stdout)?3:0;
}
