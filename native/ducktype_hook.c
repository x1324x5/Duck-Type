/*
 * ducktype_hook.c
 * -----------------
 * A minimal Windows WH_GETMESSAGE hook DLL for DuckType.
 *
 * Why this exists:
 *   A pure-Python global keyboard hook (WH_KEYBOARD_LL) only sees *physical*
 *   keystrokes -- i.e. the pinyin letters you press, NOT the Chinese characters
 *   the IME finally commits ("on-screen" text). To capture the committed text we
 *   must observe WM_CHAR / WM_IME_CHAR messages inside every GUI process. That
 *   requires a hook procedure living in a DLL that Windows injects into other
 *   processes (WH_GETMESSAGE). This file is that DLL.
 *
 * How it talks back to the Python host:
 *   - The host creates a hidden top-level window with class name
 *     "DuckTypeHostWindow".
 *   - Both sides agree on a system-wide message id via
 *     RegisterWindowMessageW(L"DuckType_CommittedChar").
 *   - For each committed character this hook does:
 *         PostMessage(host, regMsg, (WPARAM)wch, 0);
 *     Only a scalar (the UTF-16 code unit) is sent, so no cross-process pointer
 *     marshaling is needed.
 *
 * The hook is intentionally tiny and side-effect free to avoid destabilizing
 * other applications.
 */

#include <windows.h>

static HWND  g_host   = NULL;  /* cached per-process handle to the host window */
static UINT  g_msg    = 0;     /* cached registered message id                */

/* Resolve (and cache) the host window + message id inside the current process. */
static void ensure_target(void)
{
    if (g_msg == 0) {
        g_msg = RegisterWindowMessageW(L"DuckType_CommittedChar");
    }
    /* Always re-validate the cached HWND: the host may have restarted. */
    if (g_host == NULL || !IsWindow(g_host)) {
        g_host = FindWindowW(L"DuckTypeHostWindow", NULL);
    }
}

__declspec(dllexport) LRESULT CALLBACK GetMsgProc(int code, WPARAM wParam, LPARAM lParam)
{
    /* code < 0  => we must pass the call on without processing. */
    if (code >= 0 && wParam == PM_REMOVE) {
        MSG *msg = (MSG *)lParam;
        if (msg != NULL &&
            (msg->message == WM_CHAR || msg->message == WM_IME_CHAR)) {

            WCHAR wch = (WCHAR)msg->wParam;

            ensure_target();
            if (g_host != NULL && g_msg != 0) {
                /* Fire-and-forget; never block the host application. */
                PostMessageW(g_host, g_msg, (WPARAM)wch, 0);
            }
        }
    }
    return CallNextHookEx(NULL, code, wParam, lParam);
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)hinst; (void)reserved;
    switch (reason) {
        case DLL_PROCESS_ATTACH:
            DisableThreadLibraryCalls(hinst);
            break;
        default:
            break;
    }
    return TRUE;
}
