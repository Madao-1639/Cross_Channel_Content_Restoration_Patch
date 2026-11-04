/*
 * AdvHDLang.dll 替换件 v10：**位置门（正常路径）/ 白名单（仅当位置门不可用）** 的 UTF-8 字幕通路。
 *
 * 背景：AdvHD 1.9 的窄→宽唯一入口是 sub_4BA880 -> MultiByteToWideChar(932, ...)，码页是立即数；
 * `0x8f`/`0x90`（语音 + 字幕行）的文本在加载期由操作数解析器 `sub_4CD400` 现读现转。
 *
 * 判据（两条都必须满足）：
 *   ① **位置**：正在解析的 opcode ∈ {0x8f, 0x90}
 *      —— 在 `sub_4CD400` 入口（运行时 RVA 0xCD400，字节 `55 8B EC 6A FF` 与脱壳 dump 逐字节一致）
 *         写 5 字节 `E9`；序言正好 3 条完整指令（push ebp / mov ebp,esp / push -1），trampoline 原样搬。
 *         该函数是 `__thiscall`（this 在 ECX），栈上 4 个参数，**opcode = arg_8**（序言里
 *         `movzx eax,[ebp+arg_8]; mov ecx, ds:55AEB8h[eax*4]` 可证）⇒ pushal 后在 `44(%esp)`。
 *   ② **是合法 UTF-8**：否则原样走 932。这一条不是内容策略，而是防止"位置标志残留到解析返回之后"
 *      时把非 UTF-8 的串（例如安装路径里的 `†`＝CP932 0x81F5）误当 UTF-8 解读。
 *
 * **白名单兜底**：任一条件成立即转换 —— ㋐ 位置门命中且是合法 UTF-8；㋑ 输入与白名单某项全等。
 * 白名单由构建期**扫描语言包 ws2 的 8f/90 非 ASCII 字幕自动生成**（不是手工清单）⇒ 译文改动仍无需手工维护。
 * 这样即便换了 exe（RVA 失效、位置门自动退化），当前译文依旧能正确显示。
 *
 * 英文模式不加载本 DLL（它在 `zh-CN/` 里），Steam 原档一字不动。
 * 构建期由 `final_verification.check_caption_utf8()` 断言"中文包 8f/90 文本全部是合法 UTF-8"、
 * 且"根层字幕全为 ASCII"。
 *
 * 诊断：`%TEMP%\advhd_hook.log`（每次运行覆盖，只记启动摘要 + 命中情况）。
 */

#include <windows.h>

#include "whitelist.h"

typedef int (WINAPI *MB2WC_FN)(UINT CodePage, DWORD dwFlags, LPCCH lpMultiByteStr,
                               int cbMultiByte, LPWSTR lpWideCharStr, int cchWideChar);

#define PARSER_RVA 0xCD400u
#define STEAL_LEN  5u
static const unsigned char kParserSig[STEAL_LEN] = { 0x55, 0x8B, 0xEC, 0x6A, 0xFF };

static MB2WC_FN g_real_mb2wc = NULL;
static HANDLE g_log = INVALID_HANDLE_VALUE;
static LONG g_converted = 0;
static LONG g_parses = 0;
static HMODULE g_self = NULL;
static int g_parser_ok = 0;                 /* 位置门是否装上了：决定走位置还是白名单 */
static unsigned int g_cur_opcode = 0;
void *g_tramp = NULL;

static void log_open(void);
static void log_puts(const char *s);
static void log_hex(const char *s, int n);

/* 发布版（-DADVHDHOOK_SILENT）：正常运行零 IO；只有"位置门没装上"才写一行 */
#ifdef ADVHDHOOK_SILENT
#define HOOK_LOG(s) ((void)0)
#define HOOK_LOG_HEX(s, n) ((void)0)
#else
#define HOOK_LOG(s) log_puts(s)
#define HOOK_LOG_HEX(s, n) log_hex((s), (n))
#endif

static void log_open(void)
{
    if (g_log != INVALID_HANDLE_VALUE) {
        return;
    }
    char path[MAX_PATH], tmp[MAX_PATH];
    if (GetTempPathA(MAX_PATH, tmp) > 0) {
        lstrcpyA(path, tmp);
        lstrcatA(path, "advhd_hook.log");
        g_log = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    }
}

static void log_open(void);

static void log_puts(const char *s)
{
    DWORD written = 0;
    if (g_log == INVALID_HANDLE_VALUE) {
        log_open();          /* 懒打开：发布版正常运行时不会创建任何文件 */
    }
    if (g_log != INVALID_HANDLE_VALUE && s && *s) {
        WriteFile(g_log, s, lstrlenA(s), &written, NULL);
    }
}

static void log_hex(const char *s, int n)
{
    static const char *H = "0123456789abcdef";
    char buf[3 * 72 + 4];
    int i, k = 0;
    if (n > 72) n = 72;
    for (i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)s[i];
        buf[k++] = H[c >> 4];
        buf[k++] = H[c & 0xf];
        buf[k++] = ' ';
    }
    buf[k] = 0;
    log_puts(buf);
}

/* 快速判定：串里是否出现非 ASCII 字节（按 4 字节扫，纯 ASCII 直接放行）
   纯 ASCII 在 CP932 与 UTF-8 下解码结果**完全相同** ⇒ 不需要任何处理。 */
static int has_nonascii(const char *s, int n)
{
    int i = 0;
    while (i + 4 <= n) {
        DWORD w;
        memcpy(&w, s + i, 4);
        if (w & 0x80808080u) {
            return 1;
        }
        i += 4;
    }
    for (; i < n; ++i) {
        if ((unsigned char)s[i] >= 0x80) {
            return 1;
        }
    }
    return 0;
}

/* 严格 UTF-8 校验（含过长编码/代理区/截断检查）；ASCII 天然通过 */
static int is_valid_utf8(const char *s, int n)
{
    int i = 0;
    while (i < n) {
        unsigned char b = (unsigned char)s[i];
        int extra, k;
        unsigned c;
        if (b < 0x80) {
            i += 1;
            continue;
        } else if ((b & 0xE0) == 0xC0) {
            extra = 1;
            c = b & 0x1Fu;
        } else if ((b & 0xF0) == 0xE0) {
            extra = 2;
            c = b & 0x0Fu;
        } else if ((b & 0xF8) == 0xF0) {
            extra = 3;
            c = b & 0x07u;
        } else {
            return 0;
        }
        if (i + extra >= n) {
            return 0;
        }
        for (k = 1; k <= extra; ++k) {
            unsigned char t = (unsigned char)s[i + k];
            if ((t & 0xC0) != 0x80) {
                return 0;
            }
            c = (c << 6) | (t & 0x3Fu);
        }
        if ((extra == 1 && c < 0x80) || (extra == 2 && c < 0x800) || (extra == 3 && c < 0x10000)) {
            return 0;
        }
        if (c >= 0xD800 && c <= 0xDFFF) {
            return 0;
        }
        i += extra + 1;
    }
    return 1;
}

static int wl_match(const char *s, int n)
{
    int i;
    for (i = 0; i < WL_COUNT; ++i) {
        if ((int)WL_LEN[i] == n && memcmp(s, WL_PTR[i], (size_t)n) == 0) {
            return 1;
        }
    }
    return 0;
}

static int WINAPI hook_mb2wc(UINT cp, DWORD flags, LPCCH src, int cb, LPWSTR dst, int cch)
{
    int len;
    unsigned op;
    int by_place = 0;
    int take = 0;

    if (cp != 932 || src == NULL || cb == 0) {
        return g_real_mb2wc(cp, flags, src, cb, dst, cch);
    }
    len = (cb < 0) ? lstrlenA(src) : cb;
    /* 快速通道：纯 ASCII（本作绝大多数调用）→ CP932 与 UTF-8 解码结果完全相同 */
    if (len <= 0 || !has_nonascii(src, len)) {
        return g_real_mb2wc(cp, flags, src, cb, dst, cch);
    }
    op = g_cur_opcode;
    if (g_parser_ok) {
        /* 位置门可用：只按位置，不看内容 */
        by_place = (op == 0x8F || op == 0x90) && is_valid_utf8(src, len);
        take = by_place;
    } else {
        /* 位置门不可用：退回逐字节白名单 */
        take = wl_match(src, len);
    }
    if (!take) {
        return g_real_mb2wc(cp, flags, src, cb, dst, cch);
    }
    if (InterlockedIncrement(&g_converted) <= 40) {
        HOOK_LOG(by_place ? "utf8 place=1  " : "utf8 place=0 wl=1  ");
        HOOK_LOG_HEX(src, len);
        HOOK_LOG("\r\n");
    }
    return g_real_mb2wc(CP_UTF8, flags & ~((DWORD)MB_ERR_INVALID_CHARS), src, cb, dst, cch);
}

/* ---- 位置门：包装 sub_4CD400（thiscall；opcode = arg_8 = pushal 后 44(%esp)） ---- */
void set_opcode_of_parse(unsigned int op);

__attribute__((naked)) void hook_parser_entry(void)
{
    __asm__ __volatile__(
        "pushal\n\t"
        "movl 44(%esp), %eax\n\t"
        "pushl %eax\n\t"
        "call _set_opcode_of_parse\n\t"
        "addl $4, %esp\n\t"
        "popal\n\t"
        "jmp *_g_tramp\n\t"
    );
}

void set_opcode_of_parse(unsigned int op)
{
    g_cur_opcode = op & 0xFFu;
    InterlockedIncrement(&g_parses);
}

static int patch_parser(void)
{
    BYTE *base = (BYTE *)GetModuleHandleA(NULL);
    BYTE *target;
    DWORD old, img;
    IMAGE_DOS_HEADER *dos;
    IMAGE_NT_HEADERS *nt;

    if (!base) {
        return 0;
    }
    dos = (IMAGE_DOS_HEADER *)base;
    nt = (IMAGE_NT_HEADERS *)((BYTE *)base + dos->e_lfanew);
    img = nt->OptionalHeader.SizeOfImage;
    if (PARSER_RVA + STEAL_LEN > img) {
        HOOK_LOG("parser: RVA 越界（非本进程），跳过\r\n");
        return 0;
    }
    target = base + PARSER_RVA;
    if (memcmp(target, kParserSig, STEAL_LEN) != 0) {
        log_puts("parser: 序言不符，位置门失效（不转换）\r\n");
        log_hex((const char *)target, STEAL_LEN);
        log_puts("\r\n");
        return 0;
    }
    g_tramp = VirtualAlloc(NULL, 64, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_tramp) {
        return 0;
    }
    memcpy(g_tramp, kParserSig, STEAL_LEN);
    ((BYTE *)g_tramp)[STEAL_LEN] = 0xE9;
    *(INT32 *)((BYTE *)g_tramp + STEAL_LEN + 1) =
        (INT32)((target + STEAL_LEN) - ((BYTE *)g_tramp + STEAL_LEN + 5));
    if (!VirtualProtect(target, STEAL_LEN, PAGE_EXECUTE_READWRITE, &old)) {
        return 0;
    }
    target[0] = 0xE9;
    *(INT32 *)(target + 1) = (INT32)((BYTE *)hook_parser_entry - (target + 5));
    VirtualProtect(target, STEAL_LEN, old, &old);
    HOOK_LOG("parser: patched OK（位置门就绪）\r\n");
    return 1;
}

static int patch_iat(void)
{
    HMODULE base = GetModuleHandleA(NULL);
    IMAGE_DOS_HEADER *dos;
    IMAGE_NT_HEADERS *nt;
    IMAGE_IMPORT_DESCRIPTOR *imp;

    if (!base) {
        return 0;
    }
    dos = (IMAGE_DOS_HEADER *)base;
    nt = (IMAGE_NT_HEADERS *)((BYTE *)base + dos->e_lfanew);
    imp = (IMAGE_IMPORT_DESCRIPTOR *)((BYTE *)base +
          nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; imp->Name != 0; ++imp) {
        IMAGE_THUNK_DATA *orig;
        IMAGE_THUNK_DATA *thunk;
        const char *dll = (const char *)((BYTE *)base + imp->Name);
        if (imp->OriginalFirstThunk == 0) {
            continue;
        }
        orig = (IMAGE_THUNK_DATA *)((BYTE *)base + imp->OriginalFirstThunk);
        thunk = (IMAGE_THUNK_DATA *)((BYTE *)base + imp->FirstThunk);
        for (; orig->u1.Function != 0; ++orig, ++thunk) {
            IMAGE_IMPORT_BY_NAME *ibn;
            DWORD old;
            if (IMAGE_SNAP_BY_ORDINAL(orig->u1.Ordinal)) {
                continue;
            }
            ibn = (IMAGE_IMPORT_BY_NAME *)((BYTE *)base + orig->u1.AddressOfData);
            if (lstrcmpA((LPCSTR)ibn->Name, "MultiByteToWideChar") != 0) {
                continue;
            }
            if (g_real_mb2wc == NULL) {
                g_real_mb2wc = (MB2WC_FN)thunk->u1.Function;
            }
            if (!VirtualProtect(&thunk->u1.Function, sizeof(void *), PAGE_READWRITE, &old)) {
                return 0;
            }
            thunk->u1.Function = (ULONG_PTR)hook_mb2wc;
            VirtualProtect(&thunk->u1.Function, sizeof(void *), old, &old);
            HOOK_LOG("iat: hooked in ");
            HOOK_LOG(dll);
            HOOK_LOG("\r\n");
            return 1;
        }
    }
    log_puts("iat: MultiByteToWideChar not found\r\n");
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = inst;
        DisableThreadLibraryCalls(inst);
        HOOK_LOG("DllMain: attach (v10)\r\n");
        patch_iat();
        patch_parser();
        HOOK_LOG(g_parser_ok ? "mode: position\r\n" : "mode: whitelist-fallback\r\n");
        SetLastError(0);
    }
    return TRUE;
}
