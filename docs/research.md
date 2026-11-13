# Feature research

These are feasibility questions for the original EOS R5 on firmware `2.2.1`, proposed after the successful first `128 GB` SDXC sound trial. They are **not released camera features**. Each issue records stock functionality, the intended extension, implementation dependencies and verification needed. The working sound baseline stays preserved while experiments are developed separately.

| Proposal | Current evidence / next step |
| --- | --- |
| [Custom `2.71:1` XPan crop/menu mode?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/3) | Static crop-path inspection found separate display, size and output consumers. Aspect property `0x80040054` is source-derived; first verify it through an optional read-only observer. A framing overlay alone would not complete menu/output integration. |
| [JSON colour recipes and SD Picture Style bank?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/9) | A private host worksheet prototype passes five tests. Native JSON/PF3 loading, property schemas and rendering refresh remain unverified. Canon already supports PF2/PF3 in three User Def. slots. |
| [Independently calibrated HNCS-inspired profiles?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/4) | Needs controlled R5/reference captures and measurable rendering goals. No authentic HNCS import format or equivalent calibration is established. |
| [Selectable sound packs, volume and quick mute?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/7) | Builds on working speaker playback; runtime file loading, control mapping and safe active-playback cancellation need separate proof. |
| [Still-photo zebras, false colour and waveform?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/6) | Canon already has movie zebras. Investigate still-mode availability, input ranges and display-layer ownership. |
| [Anamorphic desqueeze preview?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/5) | Requires a verified preview scaler/compositor path and correct AF/touch coordinates. Initial scope would leave recorded files unchanged. |
| [Programmable exposure sequences and interval ramping?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/10) | Extends existing AEB, bracketing and timers; needs verified actuation, completion and settings-restoration contracts. |
| [Named preset libraries and session reports?](https://github.com/WhiteAvocad0/canon-r5-sound-mod/issues/8) | Canon already provides C1–C3 and ten card settings files. Investigate organization/differences and preserve unknown binary fields. |

Gemini Hydrogen research was checked against the actual target ROM and [Canon's R5 manual](https://cam.start.canon/en/C003/manual/html/index.html). ROM strings are leads, not callable function addresses. The crop property was corrected during independent branch/register review: `0x8004004f` belongs to another live-view property, while `0x80040054` reaches the `PROP_IMAGE_ASPECT_RATIO` handler. No guessed enum or new display/ISP interface has been deployed.

Colour work starts with documented [EOS Utility PF2/PF3 registration](https://cam.start.canon/en/S003/manual/html/UG-02_CameraSetting_0020.html) and [Picture Style Editor](https://cam.start.canon/en/S004/manual/html/UG-03_ImageChara_0020.html). JSON describes a recipe; it does not establish an image-processing capability. [Hasselblad's HNCS description](https://www.hasselblad.com/learn/hasselblad-natural-colour-solution/) covers a calibrated capture-through-post system, so a look with similar intent must be measured independently and clearly labelled.

Experimental source and host prototypes remain in the private research lab until their interfaces and behavior are verified. No additional camera model, firmware, crop mode or colour-processing support is implied by these issues.
