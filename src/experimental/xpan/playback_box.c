#include "playback_box.h"
const struct playback_box_binding_profile playback_box_profile = {
    {7u, 8192u, 3024u, 0u, 1220u},
    {7u, 8192u, 3024u, 0u, 0u},
    512u, 189u
};
_Static_assert(sizeof(struct playback_box_facts) == 28u, "playback scalar facts ABI");
_Static_assert(sizeof(struct playback_box_binding_profile) == 48u, "playback box profile ABI");

static uint32_t same_tuple(const struct playback_box_facts *left,
                           const uint32_t right[5])
{
    for (uint32_t i = 0; i < 5u; ++i)
        if (left->words[i] != right[i])
            return 0u;
    return 1u;
}

uint32_t playback_box_kind(const struct playback_box_facts *facts)
{
    if (!facts)
        return PB_BOX_UNKNOWN;
    if (same_tuple(facts, playback_box_profile.raw))
        return PB_BOX_RAW_L;
    if (same_tuple(facts, playback_box_profile.jpeg))
        return PB_BOX_JPEG_L;
    return PB_BOX_UNKNOWN;
}
uint32_t playback_box_ratio(const struct playback_box_facts *facts, uint32_t output[2])
{
    if (!output || playback_box_kind(facts) == PB_BOX_UNKNOWN)
        return 0u;
    output[0] = playback_box_profile.numerator;
    output[1] = playback_box_profile.denominator;
    return 1u;
}
