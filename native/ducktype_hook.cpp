/*
 * ducktype_hook.cpp
 * -----------------
 * The DuckType capture DLL, injected into every GUI process via a global
 * WH_GETMESSAGE hook. It captures committed characters through TWO paths and
 * reports each UTF-16 code unit to the Python host with a single
 *     PostMessage(host, regMsg, (WPARAM)wch, 0);
 * (only a scalar crosses the process boundary -- no pointer marshaling).
 *
 *   1. TSF path (modern apps): most current IMEs (Sogou, Microsoft Pinyin) and
 *      modern applications (WeChat, VS Code, the Win11 Notepad, browsers, ...)
 *      commit text through the Text Services Framework, which does NOT send
 *      WM_CHAR. We observe it by advising an ITfThreadMgrEventSink on the
 *      thread's TSF manager and, on focus, an ITfTextEditSink on the focused
 *      document. OnEndEdit then yields the inserted text.
 *
 *   2. WM_CHAR path (classic apps): plain Win32 edit controls (e.g. the Win+R
 *      Run box) still deliver WM_CHAR / WM_IME_CHAR. We forward those, but only
 *      when TSF is NOT actively observing this thread's document, so committed
 *      characters are never counted twice.
 *
 * The host side (Python) reassembles surrogate pairs and keeps only Han.
 */

#include <windows.h>
#include <msctf.h>
/* The TSF GUIDs (CLSID_TF_ThreadMgr, IID_ITf*, IID_IUnknown) come from the
 * platform's uuid import library: -luuid (MinGW) / uuid.lib (MSVC). */

/* ---- host channel -------------------------------------------------------- */
static HWND g_host = NULL;
static UINT g_msg  = 0;

static void ensure_target(void)
{
    if (g_msg == 0)
        g_msg = RegisterWindowMessageW(L"DuckType_CommittedChar_V4");
    if (g_host == NULL || !IsWindow(g_host))
        g_host = FindWindowW(L"DuckTypeHostWindowV4", NULL);
}

static void post_units(const WCHAR *s, ULONG n)
{
    ensure_target();
    if (g_host == NULL || g_msg == 0)
        return;
    for (ULONG i = 0; i < n; ++i)
        PostMessageW(g_host, g_msg, (WPARAM)s[i], 0);
}

/* ---- per-thread TSF state ------------------------------------------------ */
class CSink;  /* fwd */

/* A single edit that inserts more than this many code units is treated as a
 * paste / programmatic insert (not typing) and ignored, so it does not inflate
 * the statistics. A normal IME commit is at most a few characters. */
#define DT_PASTE_GUARD 30

static thread_local bool          t_tried     = false;
static thread_local ITfThreadMgr *t_tm        = nullptr;
static thread_local DWORD         t_tmCookie  = TF_INVALID_COOKIE;
static thread_local ITfContext   *t_ctx       = nullptr;
static thread_local DWORD         t_editCookie= TF_INVALID_COOKIE;
static thread_local CSink        *t_sink      = nullptr;
static thread_local bool          t_tsfActive = false;  /* TSF observing this thread */

/* ---- the COM sink (both thread-mgr and text-edit) ------------------------ */
class CSink : public ITfThreadMgrEventSink, public ITfTextEditSink
{
    LONG m_ref;
public:
    CSink() : m_ref(1) {}
    virtual ~CSink() {}

    /* IUnknown */
    STDMETHODIMP QueryInterface(REFIID riid, void **ppv)
    {
        if (!ppv) return E_POINTER;
        if (IsEqualIID(riid, IID_IUnknown) ||
            IsEqualIID(riid, IID_ITfThreadMgrEventSink))
            *ppv = static_cast<ITfThreadMgrEventSink *>(this);
        else if (IsEqualIID(riid, IID_ITfTextEditSink))
            *ppv = static_cast<ITfTextEditSink *>(this);
        else { *ppv = NULL; return E_NOINTERFACE; }
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef()  { return InterlockedIncrement(&m_ref); }
    STDMETHODIMP_(ULONG) Release() { LONG c = InterlockedDecrement(&m_ref);
                                     if (c == 0) delete this; return c; }

    /* ITfThreadMgrEventSink */
    STDMETHODIMP OnInitDocumentMgr(ITfDocumentMgr *)   { return S_OK; }
    STDMETHODIMP OnUninitDocumentMgr(ITfDocumentMgr *) { return S_OK; }
    STDMETHODIMP OnSetFocus(ITfDocumentMgr *pdimFocus, ITfDocumentMgr *);
    STDMETHODIMP OnPushContext(ITfContext *)           { return S_OK; }
    STDMETHODIMP OnPopContext(ITfContext *)            { return S_OK; }

    /* ITfTextEditSink */
    STDMETHODIMP OnEndEdit(ITfContext *pic, TfEditCookie ec, ITfEditRecord *per);
};

/* ---- attach / detach the text-edit sink on the focused document ---------- */
static void detach_edit(void)
{
    if (t_ctx && t_editCookie != TF_INVALID_COOKIE) {
        ITfSource *src = nullptr;
        if (SUCCEEDED(t_ctx->QueryInterface(IID_ITfSource, (void **)&src)) && src) {
            src->UnadviseSink(t_editCookie);
            src->Release();
        }
    }
    t_editCookie = TF_INVALID_COOKIE;
    if (t_ctx) { t_ctx->Release(); t_ctx = nullptr; }
    t_tsfActive = false;
}

static void attach_edit(ITfDocumentMgr *dim)
{
    detach_edit();
    if (!dim) return;

    ITfContext *ctx = nullptr;
    if (FAILED(dim->GetTop(&ctx)) || !ctx)
        return;

    ITfSource *src = nullptr;
    if (SUCCEEDED(ctx->QueryInterface(IID_ITfSource, (void **)&src)) && src) {
        if (SUCCEEDED(src->AdviseSink(IID_ITfTextEditSink,
                static_cast<ITfTextEditSink *>(t_sink), &t_editCookie))) {
            t_ctx = ctx;          /* keep the reference */
            t_tsfActive = true;
            ctx = nullptr;
        }
        src->Release();
    }
    if (ctx) ctx->Release();
}

STDMETHODIMP CSink::OnSetFocus(ITfDocumentMgr *pdimFocus, ITfDocumentMgr *)
{
    attach_edit(pdimFocus);
    return S_OK;
}

/*
 * True when the range is still part of an active composition -- i.e. text that
 * is sitting in the IME's candidate / preview area, NOT yet committed to the
 * document. We must not count these: an IME building a long word in pieces
 * (pick "AB", then complete "ABCD") exposes the growing composition as edits,
 * which would otherwise be counted as "AB" + "ABCD". The committed text is only
 * the final, NON-composing range, which is what we want.
 */
/* GUID_PROP_COMPOSING -- defined here rather than relying on the import lib, as
 * MinGW's uuid library does not export the TSF property GUIDs. The composing
 * property's value is a plain VT_I4 (TRUE while composing), so there is no
 * allocated VARIANT payload to free. */
static const GUID kGuidPropComposing =
    { 0xe12ac060, 0xaf15, 0x11d2, { 0xaf, 0xc5, 0x00, 0x10, 0x5a, 0x27, 0x99, 0xb5 } };

static bool range_is_composing(ITfContext *ctx, TfEditCookie ec, ITfRange *range)
{
    if (!ctx) return false;
    ITfProperty *prop = nullptr;
    if (FAILED(ctx->GetProperty(kGuidPropComposing, &prop)) || !prop)
        return false;
    bool composing = false;
    VARIANT var;
    var.vt = VT_EMPTY;
    if (SUCCEEDED(prop->GetValue(ec, range, &var))) {
        if (var.vt == VT_I4 && var.lVal != 0)
            composing = true;
    }
    prop->Release();
    return composing;
}

static void post_tsf_text(const WCHAR *s, ULONG n)
{
    post_units(s, n);   /* the Python host keeps only Han code units */
}

STDMETHODIMP CSink::OnEndEdit(ITfContext *pic, TfEditCookie ec, ITfEditRecord *per)
{
    if (!per) return S_OK;
    /* Ask for text changes AND changes to the composing property, so the edit
     * that *finalizes* a composition (clearing GUID_PROP_COMPOSING over the
     * committed range, often without changing the text itself) is delivered to
     * us -- that is the moment we want to count. */
    const GUID *props[1] = { &kGuidPropComposing };
    IEnumTfRanges *en = nullptr;
    if (FAILED(per->GetTextAndPropertyUpdates(TF_GTP_INCL_TEXT, props, 1, &en)) || !en)
        return S_OK;

    /* Accumulate this edit's committed text, then decide whether to keep it.
     * One extra slot lets us detect "more than the guard allows". */
    WCHAR acc[DT_PASTE_GUARD + 1];
    ULONG accN = 0;
    bool tooMuch = false;

    ITfRange *rg = nullptr;
    ULONG fetched = 0;
    while (en->Next(1, &rg, &fetched) == S_OK && fetched) {
        /* Skip text still in the candidate / preview area; count it only once
         * the IME finalizes it (range is no longer composing). */
        if (range_is_composing(pic, ec, rg)) {
            rg->Release();
            rg = nullptr;
            continue;
        }
        WCHAR buf[64];
        ULONG cch = 0;
        if (SUCCEEDED(rg->GetText(ec, 0, buf, 64, &cch)) && cch) {
            for (ULONG i = 0; i < cch; ++i) {
                if (accN >= DT_PASTE_GUARD) { tooMuch = true; break; }
                acc[accN++] = buf[i];
            }
            if (cch == 64) tooMuch = true;   /* range itself was large */
        }
        rg->Release();
        rg = nullptr;
        if (tooMuch) break;
    }
    en->Release();

    if (!tooMuch && accN > 0)
        post_tsf_text(acc, accN);
    return S_OK;
}

/* ---- lazily set up TSF observation for the current (UI) thread ----------- */
static void ensure_tsf(void)
{
    if (t_tried) return;
    t_tried = true;

    HRESULT hrco = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (hrco == RPC_E_CHANGED_MODE)
        return;  /* MTA thread -- cannot observe TSF here; WM_CHAR path remains */

    ITfThreadMgr *tm = nullptr;
    if (FAILED(CoCreateInstance(CLSID_TF_ThreadMgr, nullptr, CLSCTX_INPROC_SERVER,
                                IID_ITfThreadMgr, (void **)&tm)) || !tm)
        return;
    t_tm = tm;
    t_sink = new CSink();

    ITfSource *src = nullptr;
    if (SUCCEEDED(tm->QueryInterface(IID_ITfSource, (void **)&src)) && src) {
        src->AdviseSink(IID_ITfThreadMgrEventSink,
                        static_cast<ITfThreadMgrEventSink *>(t_sink), &t_tmCookie);
        src->Release();
    }

    /* Catch a document that already has focus (sink advised after the fact). */
    ITfDocumentMgr *dim = nullptr;
    if (SUCCEEDED(tm->GetFocus(&dim)) && dim) {
        attach_edit(dim);
        dim->Release();
    }
}

/* ---- the injected hook procedure ---------------------------------------- */
extern "C" __declspec(dllexport)
LRESULT CALLBACK GetMsgProc(int code, WPARAM wParam, LPARAM lParam)
{
    if (code >= 0 && wParam == PM_REMOVE) {
        ensure_tsf();
        MSG *msg = (MSG *)lParam;
        if (msg != NULL &&
            (msg->message == WM_CHAR || msg->message == WM_IME_CHAR)) {
            /* Only use the legacy path when TSF is not handling this thread,
             * so committed text is never double-counted. */
            if (!t_tsfActive) {
                WCHAR wch = (WCHAR)msg->wParam;
                post_units(&wch, 1);
            }
        }
    }
    return CallNextHookEx(NULL, code, wParam, lParam);
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    switch (reason) {
        case DLL_PROCESS_ATTACH:
            DisableThreadLibraryCalls(hinst);
            /*
             * CRITICAL: pin ourselves so this DLL is NEVER unloaded mid-process.
             * We register TSF sinks (ITfTextEditSink, ...) inside every host
             * application. When DuckType exits it calls UnhookWindowsHookEx,
             * which would unload this DLL from those apps -- but MSCTF still
             * holds pointers to our sink objects and would later call into the
             * now-unmapped code, crashing the host (e.g. WeChat/QQ in MSCTF.dll).
             * Pinning keeps the code mapped until the host process itself exits,
             * so those callbacks always land on valid code. After DuckType quits
             * the sinks simply PostMessage to a window that no longer exists,
             * which is a harmless no-op.
             */
            {
                HMODULE self = NULL;
                GetModuleHandleExW(
                    GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                    GET_MODULE_HANDLE_EX_FLAG_PIN,
                    reinterpret_cast<LPCWSTR>(&DllMain), &self);
            }
            break;
        default:
            break;
    }
    return TRUE;
}
