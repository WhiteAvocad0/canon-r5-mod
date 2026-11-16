#ifndef R5_PLAYBACK_BOX_H
#define R5_PLAYBACK_BOX_H
#include <stdint.h>

enum playback_box_kind { PB_BOX_UNKNOWN = 0, PB_BOX_RAW_L = 1, PB_BOX_JPEG_L = 2 };
struct playback_box_facts {
    uint32_t words[5];
    /* Current O+3E/+40 (or its cache copy), not file dimensions. Native
     * FHD replacement can change them without changing the file crop tuple. */
    uint32_t current_width, current_height;
};
struct playback_box_binding_profile {
    uint32_t raw[5], jpeg[5];
    uint32_t numerator, denominator;
};
extern const struct playback_box_binding_profile playback_box_profile;
/* Only the exact, source-validated file tuple owns crop identity. This pure
 * classifier does not admit a native pointer or validate current dimensions. */
uint32_t playback_box_kind(const struct playback_box_facts *facts);
uint32_t playback_box_ratio(const struct playback_box_facts *facts, uint32_t output[2]);
#endif
