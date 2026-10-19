/* Offline contract test: compile SDL's actual patched JoystickOpen function
 * against inert DirectInput stubs. No Windows device or SDL initialization. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned short Uint16;
typedef int SDL_bool;
typedef int HRESULT;
#define SDL_TRUE 1
#define SDL_FALSE 0
#define FAILED(x) ((x) < 0)
#define DISCL_EXCLUSIVE 1
#define DISCL_NONEXCLUSIVE 2
#define DISCL_BACKGROUND 8
#define DIDC_FORCEFEEDBACK 256
#define DISFFC_RESET 1
#define DIPH_DEVICE 0
#define DIPROPAUTOCENTER_ON 1
#define DIPROP_AUTOCENTER 1
#define DIPROP_BUFFERSIZE 2
#define DIDFT_AXIS 3
#define DIDFT_BUTTON 12
#define DIDFT_POV 16
#define DI_POLLEDDEVICE 2
#define DIERR_INPUTLOST -1
#define DIERR_NOTACQUIRED -2
#define INPUT_QSIZE 128
#define SDL_zero(x) memset(&(x), 0, sizeof(x))
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); exit(1); } } while (0)

typedef struct { int dwSize, dwHeaderSize, dwObj, dwHow; } DIPROPHEADER;
typedef struct { DIPROPHEADER diph; int dwData; } DIPROPDWORD;
typedef struct { int dwSize, dwFlags; } DIDEVCAPS;
typedef struct { Uint16 vendor, product; } SDL_JoystickGUID;
typedef struct { SDL_JoystickGUID guid; struct { int guidInstance; } dxdevice; } JoyStick_DeviceData;
typedef struct { int buffered; DIDEVCAPS Capabilities; void *InputDevice; } Hardware;
typedef struct { Hardware *hwdata; } SDL_Joystick;

static int hint, force_capable, cooperative, resets, autocenters, acquires, unacquires;
static int enums, sorts, formats, buffers, polls, delays, creates, fail_at, calls, polled;
static const void *dinput = NULL, *SDL_HelperWindow = NULL;
static const int SDL_c_dfDIJoystick2 = 0, EnumDevObjectsCallback = 0;
static int result(void) { return ++calls == fail_at ? -10 : 0; }
static int SetDIerror(const char *name, int error) { (void)name; return error; }
static void SDL_GetJoystickGUIDInfo(SDL_JoystickGUID guid, Uint16 *vendor, Uint16 *product, void *version, void *crc)
{ *vendor = guid.vendor; *product = guid.product; (void)version; (void)crc; }
static int SDL_GetHintBoolean(const char *key, int fallback)
{ CHECK(strcmp(key, "P8_TSXW_PRESERVE_FORCES") == 0); (void)fallback; return hint; }
static int mock_create(const void *input, int *guid, void **device, void *outer)
{ (void)input; (void)guid; (void)outer; *device = (void *)1; creates++; return result(); }
static int mock_cooperate(void *device, const void *window, int flags)
{ (void)device; (void)window; cooperative = flags; return result(); }
static int mock_format(void *device, const int *format)
{ (void)device; CHECK(format == &SDL_c_dfDIJoystick2); formats++; return result(); }
static int mock_capabilities(void *device, DIDEVCAPS *cap)
{ (void)device; CHECK(cap->dwSize == sizeof(*cap)); cap->dwFlags = force_capable ? DIDC_FORCEFEEDBACK : 0; return result(); }
static int mock_acquire(void *device) { (void)device; acquires++; return result(); }
static int mock_unacquire(void *device) { (void)device; unacquires++; return result(); }
static int mock_force(void *device, int command)
{ (void)device; CHECK(command == DISFFC_RESET); resets++; return result(); }
static int mock_property(void *device, int property, DIPROPHEADER *header)
{
    DIPROPDWORD *value = (DIPROPDWORD *)header; (void)device;
    CHECK(header->dwSize == sizeof(*value) && header->dwHeaderSize == sizeof(*header));
    CHECK(header->dwHow == DIPH_DEVICE && header->dwObj == 0);
    if (property == DIPROP_AUTOCENTER) { autocenters++; CHECK(value->dwData == DIPROPAUTOCENTER_ON); }
    else { CHECK(property == DIPROP_BUFFERSIZE && value->dwData == INPUT_QSIZE); buffers++; if (polled) return DI_POLLEDDEVICE; }
    return result();
}
static int mock_enum(void *device, int callback, SDL_Joystick *joystick, int flags)
{ (void)device; (void)callback; (void)joystick; CHECK(flags == (DIDFT_AXIS | DIDFT_BUTTON | DIDFT_POV)); enums++; return result(); }
static void SortDevObjects(SDL_Joystick *joystick) { (void)joystick; sorts++; }
static int mock_poll(void *device) { (void)device; return ++polls == 1 ? DIERR_NOTACQUIRED : 0; }
static void SDL_Delay(int ms) { CHECK(ms == 50); delays++; }

#define IDirectInput8_CreateDevice mock_create
#define IDirectInputDevice8_SetCooperativeLevel mock_cooperate
#define IDirectInputDevice8_SetDataFormat mock_format
#define IDirectInputDevice8_GetCapabilities mock_capabilities
#define IDirectInputDevice8_Acquire mock_acquire
#define IDirectInputDevice8_Unacquire mock_unacquire
#define IDirectInputDevice8_SendForceFeedbackCommand mock_force
#define IDirectInputDevice8_SetProperty mock_property
#define IDirectInputDevice8_EnumObjects mock_enum
#define IDirectInputDevice8_Poll mock_poll

#include "actual_open.c"

static int run_case(int vendor, int product, int enabled, int forces, int error_at, int polling)
{
    Hardware hardware = {0}; SDL_Joystick joystick = {&hardware};
    JoyStick_DeviceData device = {{(Uint16)vendor, (Uint16)product}, {1}};
    int preserve = vendor == 0x044f && product == 0xb692 && enabled;
    int rc;
    hint = enabled; force_capable = forces; fail_at = error_at; polled = polling;
    cooperative = resets = autocenters = acquires = unacquires = enums = sorts = 0;
    formats = buffers = polls = delays = creates = calls = 0;
    rc = SDL_DINPUT_JoystickOpen(&joystick, &device);
    if (preserve) { CHECK(resets == 0 && autocenters == 0 && unacquires == 0); }
    /* SDL 2.28.4 deliberately leaves EnumObjects' result unchecked; keep that
       existing behavior too. Other checked input setup failures propagate. */
    if (error_at && error_at != 5) { CHECK(rc < 0); return 1; }
    CHECK(rc == 0);
    CHECK(cooperative == ((preserve ? DISCL_NONEXCLUSIVE : DISCL_EXCLUSIVE) | DISCL_BACKGROUND));
    CHECK(creates == 1 && formats == 1 && enums == 1 && sorts == 1 && buffers == 1);
    CHECK(polls == 2 && delays == 1 && acquires == 1 + (!preserve && forces));
    CHECK(resets == (!preserve && forces) && autocenters == (!preserve && forces));
    CHECK(hardware.buffered == !polling);
    return 1;
}

int main(void)
{
    int enabled, forces, polling, index, cases = 0;
    for (enabled = 0; enabled < 2; enabled++)
        for (forces = 0; forces < 2; forces++)
            for (polling = 0; polling < 2; polling++) {
                cases += run_case(0x044f, 0xb692, enabled, forces, 0, polling);
                cases += run_case(0x044f, 0xb693, enabled, forces, 0, polling);
                cases += run_case(0x0450, 0xb692, enabled, forces, 0, polling);
            }
    /* Input setup failures still propagate; preservation never falls back to
       exclusive acquisition or force reset. */
    for (index = 1; index <= 6; index++)
        cases += run_case(0x044f, 0xb692, 1, 1, index, 0);
    printf("%d native open contract cases passed (no device access)\n", cases);
    return 0;
}
