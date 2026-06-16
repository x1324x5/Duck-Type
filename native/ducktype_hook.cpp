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

/* ---- per-process single-poster election ---------------------------------
 * Several copies of this DLL can be live in ONE host process at the same time:
 * a previous DuckType build pinned its hook DLL here (we pin on purpose -- see
 * DllMain), and an updated DuckType injects a fresh copy without the old one
 * ever unloading. If every copy posted, the host would count each committed
 * character 2x/3x... To avoid that -- WITHOUT forcing the user to reboot after
 * every update -- the copies elect a SINGLE poster per host *generation* (the
 * host window is recreated each launch, so its HWND is the generation token).
 * Election state lives in a per-process named shared section so it is visible
 * across the distinct module mappings; the elected owner is the address of a
 * module-unique tag (process-wide unique, comparable through the shared
 * section). When the host restarts the generation changes and the first copy to
 * handle an event re-claims, so capture always continues with exactly one
 * poster. NOTE: this only coordinates copies that contain this logic; a legacy
 * DLL predating it is shed instead by the host-channel version bump (...V5 ->
 * ...V6 below), so it can no longer find this host window. */
struct DtElect { LONGLONG host; LONGLONG owner; };
static char     g_moduleTag  = 0;          /* &g_moduleTag is unique per module */
static HANDLE   g_electMap   = NULL;
static DtElect *g_elect      = NULL;
static HANDLE   g_electMtx   = NULL;
static HWND     g_electedFor  = (HWND)(LONG_PTR)-1;
static bool     g_amPoster   = true;       /* fail-open until told otherwise */

static void elect_open(void)
{
    if (g_elect && g_electMtx) return;
    WCHAR name[64], mname[64];
    DWORD pid = GetCurrentProcessId();
    wsprintfW(name,  L"Local\\DuckTypeElect6_%lu", pid);
    wsprintfW(mname, L"Local\\DuckTypeElectMtx6_%lu", pid);
    if (!g_electMtx)
        g_electMtx = CreateMutexW(NULL, FALSE, mname);
    if (!g_electMap) {
        g_electMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
                                        0, sizeof(DtElect), name);
        if (g_electMap)
            g_elect = (DtElect *)MapViewOfFile(g_electMap, FILE_MAP_ALL_ACCESS,
                                               0, 0, sizeof(DtElect));
    }
}

/* Decide, once per host generation, whether THIS module copy is the poster. */
static void elect_for_host(void)
{
    if (g_host == g_electedFor) return;        /* already decided for this gen */
    g_electedFor = g_host;
    g_amPoster   = true;                        /* fail-open if no shared state */
    if (g_host == NULL) return;
    elect_open();
    if (!g_elect || !g_electMtx) return;
    WaitForSingleObject(g_electMtx, 1000);
    if (g_elect->host != (LONGLONG)(LONG_PTR)g_host) {
        g_elect->host  = (LONGLONG)(LONG_PTR)g_host;
        g_elect->owner = (LONGLONG)(LONG_PTR)&g_moduleTag;   /* claim this gen */
    }
    g_amPoster = (g_elect->owner == (LONGLONG)(LONG_PTR)&g_moduleTag);
    ReleaseMutex(g_electMtx);
}

static void ensure_target(void)
{
    if (g_msg == 0)
        g_msg = RegisterWindowMessageW(L"DuckType_CommittedChar_V6");
    if (g_host == NULL || !IsWindow(g_host))
        g_host = FindWindowW(L"DuckTypeHostWindowV6", NULL);
    elect_for_host();
}

static void post_units(const WCHAR *s, ULONG n)
{
    ensure_target();
    if (g_host == NULL || g_msg == 0 || !g_amPoster)
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
static thread_local DWORD         t_compCookie= TF_INVALID_COOKIE;
static thread_local CSink        *t_sink      = nullptr;
static thread_local bool          t_tsfActive = false;  /* TSF observing this thread */
/* True once the composition-owner sink was successfully advised on the focused
 * document, i.e. we are able to observe its IME composition lifecycle. When this
 * is false we cannot tell typed text from inserted text, so we must NOT gate
 * (fail open) -- otherwise we'd silently drop real input. */
static thread_local bool          t_compSinkOk = false;
/* True once we have seen a real IME COMPOSITION in the currently focused
 * document (its composition started/ended, or a composing range appeared).
 * Committed text is only trusted after that. This rejects text a control
 * inserts on its own -- e.g. QQ's "全员禁言中" placeholder shown in a muted
 * group's input box -- which the user never typed and which never goes through
 * a composition. Real Han input always composes first, so genuine typing is
 * never dropped. The signal is driven primarily by the composition-owner sink
 * (which fires even in apps -- WeChat, explorer, Electron/Chromium like Claude
 * -- that don't expose a composing *range* to GUID_PROP_COMPOSING). Reset on
 * focus change. */
static thread_local bool          t_everComposed = false;

/* ---- the COM sink (thread-mgr + text-edit + composition-owner) ----------- */
class CSink : public ITfThreadMgrEventSink,
              public ITfTextEditSink,
              public ITfContextOwnerCompositionSink
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
        else if (IsEqualIID(riid, IID_ITfContextOwnerCompositionSink))
            *ppv = static_cast<ITfContextOwnerCompositionSink *>(this);
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

    /* ITfContextOwnerCompositionSink -- the document's IME composition
     * lifecycle. A composition starting or ending proves this document does
     * real IME input, which unlocks counting its committed text (t_everComposed)
     * even when no composing *range* is ever exposed. */
    STDMETHODIMP OnStartComposition(ITfCompositionView *, WINBOOL *pfOk)
    { t_everComposed = true; if (pfOk) *pfOk = TRUE; return S_OK; }
    STDMETHODIMP OnUpdateComposition(ITfCompositionView *, ITfRange *) { return S_OK; }
    STDMETHODIMP OnEndComposition(ITfCompositionView *)
    { t_everComposed = true; return S_OK; }
};

/* ---- attach / detach the text-edit sink on the focused document ---------- */
static void detach_edit(void)
{
    if (t_ctx && (t_editCookie != TF_INVALID_COOKIE ||
                  t_compCookie != TF_INVALID_COOKIE)) {
        ITfSource *src = nullptr;
        if (SUCCEEDED(t_ctx->QueryInterface(IID_ITfSource, (void **)&src)) && src) {
            if (t_editCookie != TF_INVALID_COOKIE) src->UnadviseSink(t_editCookie);
            if (t_compCookie != TF_INVALID_COOKIE) src->UnadviseSink(t_compCookie);
            src->Release();
        }
    }
    t_editCookie = TF_INVALID_COOKIE;
    t_compCookie = TF_INVALID_COOKIE;
    if (t_ctx) { t_ctx->Release(); t_ctx = nullptr; }
    t_tsfActive = false;
    t_compSinkOk = false;
    t_everComposed = false;   /* a new document hasn't composed anything yet */
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
        /* Also observe the composition lifecycle, so we can tell real IME input
         * from text a control inserts on its own even where no composing range
         * is exposed. Failure here leaves t_compSinkOk false -> gate fails open. */
        if (t_tsfActive &&
            SUCCEEDED(src->AdviseSink(IID_ITfContextOwnerCompositionSink,
                static_cast<ITfContextOwnerCompositionSink *>(t_sink), &t_compCookie)))
            t_compSinkOk = true;
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
    /* Enumerate ONLY the ranges whose composing property changed -- i.e. the
     * moment a composition finalizes (GUID_PROP_COMPOSING cleared over the
     * committed range). We deliberately do NOT pass TF_GTP_INCL_TEXT: an IME
     * that re-inserts the committed text on finalize would otherwise expose that
     * same range twice (once as a text change, once as a property change),
     * making us count every word twice. The property change alone fires for both
     * "re-insert on commit" and "just clear the attribute" IMEs. */
    const GUID *props[1] = { &kGuidPropComposing };
    IEnumTfRanges *en = nullptr;
    if (FAILED(per->GetTextAndPropertyUpdates(0, props, 1, &en)) || !en)
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
         * the IME finalizes it (range is no longer composing). Seeing a
         * composing range also proves this document does real IME input, which
         * unlocks counting its committed text (see t_everComposed). */
        if (range_is_composing(pic, ec, rg)) {
            t_everComposed = true;
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

    /* Only trust committed text once this document has shown a composition --
     * unless we couldn't observe its composition lifecycle at all, in which case
     * we must fail open (post) rather than risk dropping real input. Without the
     * gate, a control that programmatically inserts non-composing text (e.g. QQ's
     * "全员禁言中" placeholder) would be miscounted as typing. */
    if (!tooMuch && accN > 0 && (t_everComposed || !t_compSinkOk))
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
