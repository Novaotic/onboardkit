"""Admin authentication helpers.

Authentication flow
-------------------
1. Bind as the user to verify credentials (proves correct password).
2. Use the same live connection to look up the user's own AD object and read
   their ``memberOf`` attribute (self-read is always permitted in AD).
3. If the self-lookup fails, fall back to a service account connection
   (LDAP_BIND_DN / LDAP_BIND_PASSWORD).
4. Check that the user is a member of LDAP_ADMIN_GROUP using the AD
   transitive-membership OID so nested groups work correctly.

Dev fallback
------------
If LDAP_HOST is blank, authenticate against ADMIN_USERNAME / ADMIN_PASSWORD
env vars instead. Intended for local development only — not for production.
"""

import logging
import os

from ldap3 import (
    ANONYMOUS,
    AUTO_BIND_NO_TLS,
    BASE,
    DSA,
    SAFE_SYNC,
    Connection,
    Server,
)
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

log = logging.getLogger(__name__)

# AD OID for transitive (nested) group membership matching
_MEMBER_OF_RECURSIVE = "memberOf:1.2.840.113556.1.4.1941:"


def _search(conn: Connection, **kwargs):
    """Run an ldap3 search and return (ok, result_dict, response_list).

    With thread-safe strategies (e.g. SAFE_SYNC), ldap3 returns a tuple:
    (status, result, response, request) and does not reliably populate
    conn.entries/conn.response for the calling thread. We must use the returned
    response directly.
    """
    out = conn.search(**kwargs)
    if isinstance(out, tuple) and len(out) >= 3:
        ok = bool(out[0])
        result = out[1] or {}
        response = out[2] or []
        return ok, result, response
    return bool(out), conn.result or {}, conn.response or []


def _entries_from_response(response_list: list[dict]) -> list[dict]:
    return [r for r in (response_list or []) if r.get("type") == "searchResEntry"]


def _ldap_socket_timeouts() -> tuple[int, int]:
    """TCP connect timeout and per-operation socket read timeout (seconds).

    Without these, a silent DC/network failure can block the worker for a long
    time and on Windows Ctrl+C may not interrupt a stuck socket read.
    Override with LDAP_CONNECT_TIMEOUT and LDAP_RECEIVE_TIMEOUT in .env.
    """
    def _clamp(env_name: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(os.getenv(env_name, str(default)).strip())
            return max(lo, min(v, hi))
        except ValueError:
            return default

    return (
        _clamp("LDAP_CONNECT_TIMEOUT", 10, 3, 120),
        _clamp("LDAP_RECEIVE_TIMEOUT", 25, 5, 300),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def authenticate_user(username: str, password: str) -> bool:
    """Return True if credentials are valid and the user is in the admin group."""
    if not username or not password:
        return False

    ldap_host = os.getenv("LDAP_HOST", "").strip()
    if not ldap_host:
        log.debug("LDAP_HOST not set — using ADMIN_USERNAME / ADMIN_PASSWORD fallback")
        return _check_env_credentials(username, password)

    return _ldap_bind(username, password, ldap_host)


def admin_auth_uses_env_fallback() -> bool:
    """True when admin sign-in uses .env credentials instead of LDAP."""
    return not os.getenv("LDAP_HOST", "").strip()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _check_env_credentials(username: str, password: str) -> bool:
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "")
    return bool(admin_pass) and username == admin_user and password == admin_pass


def _ldap_bind(username: str, password: str, ldap_host: str) -> bool:
    ldap_domain   = os.getenv("LDAP_DOMAIN",    "").strip()
    ldap_port     = int(os.getenv("LDAP_PORT",  "389"))
    use_ssl       = ldap_port == 636
    ldap_base_dn  = os.getenv("LDAP_BASE_DN",   "").strip()
    ldap_admin_group = os.getenv("LDAP_ADMIN_GROUP", "").strip()

    # Strip domain suffix if the user typed user@domain instead of just user
    sam_account   = username.split("@")[0]
    user_principal = f"{sam_account}@{ldap_domain}" if ldap_domain else sam_account

    connect_t, receive_t = _ldap_socket_timeouts()
    log.debug(
        "LDAP bind: host=%s port=%d ssl=%s upn=%s timeouts(connect=%ss receive=%ss)",
        ldap_host, ldap_port, use_ssl, user_principal, connect_t, receive_t,
    )

    # DSA: after bind, ldap3 performs the same Root DSE read as _get_dsa_info
    # (get_operational_attributes=True, attr list includes * and +). Using
    # get_info=None skips that — which left namingContexts empty on your DC.
    server = Server(
        ldap_host,
        port=ldap_port,
        use_ssl=use_ssl,
        get_info=DSA,
        connect_timeout=connect_t,
    )
    try:
        conn = Connection(
            server,
            user=user_principal,
            password=password,
            client_strategy=SAFE_SYNC,
            auto_bind=AUTO_BIND_NO_TLS,
            raise_exceptions=True,
            auto_referrals=False,
            receive_timeout=receive_t,
        )
    except LDAPException as exc:
        log.warning("LDAP bind failed for %s: %s: %s", user_principal, type(exc).__name__, exc)
        return False

    log.debug("LDAP bind succeeded for %s", user_principal)

    if not (ldap_admin_group and ldap_base_dn):
        conn.unbind()
        log.info("LDAP admin login OK (no group check configured): %s", user_principal)
        return True

    # Naming contexts: prefer ldap3's DSA info (filled during bind when get_info=DSA).
    root_info = _naming_info_from_ldap3_server(server)
    if not root_info.get("defaultNamingContext") and not root_info.get("namingContexts"):
        root_info = _read_root_dse(conn)
    if not root_info.get("defaultNamingContext") and not root_info.get("namingContexts"):
        anon_info = _read_root_dse_anonymous(ldap_host, ldap_port, use_ssl)
        if anon_info.get("defaultNamingContext") or anon_info.get("namingContexts"):
            log.debug("RootDSE (anonymous bind) returned naming info")
            root_info = anon_info
    default_nc = root_info.get("defaultNamingContext")
    naming_ctx = root_info.get("namingContexts") or []
    if default_nc:
        log.debug("RootDSE defaultNamingContext=%s", default_nc)
    if naming_ctx:
        log.debug("RootDSE namingContexts (%d): %s", len(naming_ctx), naming_ctx[:5])
    if not default_nc and not naming_ctx:
        log.debug("RootDSE had no naming info; using LDAP_BASE_DN / derived bases")

    log.debug("LDAP group check against %s", ldap_admin_group)
    in_group = _is_in_group(
        server,
        conn,
        ldap_base_dn,
        ldap_admin_group,
        sam_account,
        user_principal,
        ldap_domain=ldap_domain,
        default_nc=default_nc,
        naming_contexts=naming_ctx,
    )
    conn.unbind()

    if in_group:
        log.info("LDAP admin login OK: %s", user_principal)
    else:
        log.warning("LDAP admin denied (not in allowed group): %s", user_principal)
    return in_group


def _is_in_group(
    server: Server,
    user_conn: Connection,
    base_dn: str,
    group_dn: str,
    sam_account: str,
    user_principal: str,
    ldap_domain: str = "",
    default_nc: str | None = None,
    naming_contexts: list[str] | None = None,
) -> bool:
    """Return True if the user is a member of group_dn (direct or nested).

    Tries the user's own connection first (self-read is always allowed in AD),
    then falls back to a service account if the self-lookup returns nothing.
    """
    # Prefer RootDSE defaultNamingContext for group recursive search.
    domain_root = default_nc or ",".join(
        p for p in base_dn.split(",") if p.strip().upper().startswith("DC=")
    ) or base_dn

    search_bases = _search_base_candidates(base_dn, domain_root, ldap_domain, naming_contexts)
    log.debug("LDAP user search bases: %s", search_bases)

    # Parse who_am_i first so sAMAccountName matches AD (login name may differ from UPN prefix).
    who = _safe_who_am_i(user_conn)
    if who and who.startswith("u:"):
        try:
            netbios, sam = who[2:].split("\\", 1)
            log.debug("who_am_i → netbios=%s sam=%s", netbios, sam)
            sam_account = sam
        except ValueError:
            pass

    upn_variants = _upn_variants(sam_account, ldap_domain)
    if len(upn_variants) > 1:
        log.debug("UPN variants: %s", upn_variants)

    # ── Strategy 1: ask AD "who am I?" then BASE-read that DN ────────────────
    if who and who.startswith("dn:"):
        user_dn = who[3:].strip()
        entry = _read_user_by_dn(user_conn, user_dn)
        if entry is not None:
            return _membership_check(user_conn, domain_root, entry, group_dn, user_dn=user_dn)

    # ── Strategy 1b: subtree search as the bound user (same bases / UPN variants)
    entry = _find_user_multi(
        user_conn, search_bases, sam_account, upn_variants, label="user connection"
    )
    if entry is not None:
        user_dn = str(entry.distinguishedName) if entry.distinguishedName else None
        eff_root = _dn_to_domain_root(user_dn) or domain_root
        if eff_root != domain_root:
            log.debug("Using domain NC %s for group check (was %s)", eff_root, domain_root)
        return _membership_check(user_conn, eff_root, entry, group_dn, user_dn=user_dn)

    # ── Strategy 2: service account connection ────────────────────────────────
    bind_dn = os.getenv("LDAP_BIND_DN", "").strip()
    bind_pw = os.getenv("LDAP_BIND_PASSWORD", "").strip()
    ldap_host = os.getenv("LDAP_HOST", "").strip()

    if bind_dn and bind_pw:
        entry = None
        svc_conn = None
        # Prefer Global Catalog for user resolution when configured (matches many Wiki.js setups).
        svc_ports: list[int] = []
        gc_env = os.getenv("LDAP_GC_PORT", "").strip()
        if gc_env:
            try:
                svc_ports.append(int(gc_env))
            except ValueError:
                log.warning("Invalid LDAP_GC_PORT=%r; ignoring", gc_env)
        if 389 not in svc_ports:
            svc_ports.append(389)
        connect_t, receive_t = _ldap_socket_timeouts()
        for svc_port in svc_ports:
            svc_conn = None
            try:
                svc_server = Server(
                    ldap_host,
                    port=svc_port,
                    use_ssl=False,
                    get_info=DSA,
                    connect_timeout=connect_t,
                )
                svc_conn = Connection(
                    svc_server,
                    user=bind_dn,
                    password=bind_pw,
                    client_strategy=SAFE_SYNC,
                    auto_bind=AUTO_BIND_NO_TLS,
                    raise_exceptions=True,
                    auto_referrals=False,
                    receive_timeout=receive_t,
                )
                log.debug("Service account bind OK on port %s", svc_port)
                entry = _find_user_multi(
                    svc_conn, search_bases, sam_account, upn_variants,
                    label=f"service account @{svc_port}",
                )
                if entry is not None:
                    user_dn = str(entry.distinguishedName) if entry.distinguishedName else None
                    eff_root = _dn_to_domain_root(user_dn) or domain_root
                    if eff_root != domain_root:
                        log.debug("Using domain NC %s for group check (was %s)", eff_root, domain_root)
                    result = _membership_check(svc_conn, eff_root, entry, group_dn, user_dn=user_dn)
                    svc_conn.unbind()
                    return result
            except LDAPException as exc:
                log.warning("LDAP service account failed on port %s: %s", svc_port, exc)
            finally:
                if svc_conn is not None and svc_conn.bound:
                    svc_conn.unbind()
    else:
        log.warning("LDAP_BIND_DN / LDAP_BIND_PASSWORD not set; skipping service-account fallback")

    log.warning("LDAP admin denied (could not resolve user in directory): %s", sam_account)
    return False


def _search_base_candidates(
    base_dn: str,
    domain_root: str,
    ldap_domain: str,
    naming_contexts: list[str] | None,
) -> list[str]:
    """Ordered list of DNs to search under (no empty string — AD returns noSuchObject)."""
    extra = os.getenv("LDAP_SEARCH_EXTRA_BASES", "").strip()
    extras = [x.strip() for x in extra.split(",") if x.strip()]

    # Guess DC=… from DNS only when it matches a real partition from RootDSE.
    # A wrong guess (e.g. DC=example,DC=org when the domain is DC=ad,DC=example,DC=org)
    # causes AD referrals or odd client behavior and on Windows often ends in
    # WinError 10060 (connection timeout).
    derived_dns: list[str] = []
    if ldap_domain:
        guesses: list[str] = []
        g = _domain_to_ldap_dn(ldap_domain)
        if g:
            guesses.append(g)
        if "." in ldap_domain and not ldap_domain.lower().startswith("ad."):
            c = _domain_to_ldap_dn(f"ad.{ldap_domain}")
            if c:
                guesses.append(c)
        nc_set = {str(x) for x in (naming_contexts or [])}
        if nc_set:
            derived_dns = [x for x in guesses if x in nc_set]
        else:
            derived_dns = guesses

    raw: list[str] = []
    if base_dn:
        raw.append(base_dn)
    if domain_root:
        raw.append(domain_root)
    for d in derived_dns:
        raw.append(d)
    raw.extend(extras)
    if naming_contexts:
        for nc in naming_contexts:
            nu = nc.upper()
            if not nu.startswith("DC="):
                continue
            # DNS application partitions — never contain user objects; they only slow searches.
            if "DOMAINDNSZONES" in nu or "FORESTDNSZONES" in nu:
                continue
            raw.append(nc)

    seen: set[str] = set()
    out: list[str] = []
    for b in raw:
        if b and b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _dn_to_domain_root(dn: str | None) -> str | None:
    """Extract NC from a DN: CN=...,OU=...,DC=ad,DC=example,DC=org → DC=ad,DC=example,DC=org"""
    if not dn:
        return None
    parts = [p.strip() for p in dn.split(",") if p.strip().upper().startswith("DC=")]
    return ",".join(parts) if parts else None


def _domain_to_ldap_dn(domain: str) -> str:
    """DNS name to domain NC, e.g. example.com → DC=example,DC=com"""
    parts = [p for p in domain.strip().lower().split(".") if p]
    if not parts:
        return ""
    return ",".join(f"DC={p}" for p in parts)


def _upn_variants(sam: str, ldap_domain: str) -> list[str]:
    if not ldap_domain:
        return []
    v = [f"{sam}@{ldap_domain}"]
    if "." in ldap_domain and not ldap_domain.lower().startswith("ad."):
        v.append(f"{sam}@ad.{ldap_domain}")
    return list(dict.fromkeys(v))


def _find_user_multi(
    conn: Connection,
    search_bases: list[str],
    sam_account: str,
    upn_variants: list[str],
    label: str,
) -> object | None:
    filters = _find_user_search_filters(sam_account, upn_variants)

    for search_base in search_bases:
        for fltr in filters:
            try:
                ok, result, response = _search(
                    conn,
                    search_base=search_base,
                    search_filter=fltr,
                    attributes=[
                        "memberOf",
                        "distinguishedName",
                        "sAMAccountName",
                        "userPrincipalName",
                    ],
                )
                entry_responses = _entries_from_response(response)
                if entry_responses:
                    # Convert first entry response to ldap3 entry by reusing conn.entries
                    # when available; otherwise keep using raw response attributes.
                    # Best-effort: prefer exact sAMAccountName / UPN match in raw responses.
                    chosen = entry_responses[0]
                    if len(entry_responses) > 1:
                        want_sam = sam_account.lower()
                        want_upn = {v.lower() for v in upn_variants}
                        for cand in entry_responses:
                            attrs = cand.get("attributes") or {}
                            sam = (attrs.get("sAMAccountName") or [""])[0]
                            if str(sam).lower() == want_sam:
                                chosen = cand
                                break
                        else:
                            for cand in entry_responses:
                                attrs = cand.get("attributes") or {}
                                upn = (attrs.get("userPrincipalName") or [""])[0]
                                if str(upn).lower() in want_upn:
                                    chosen = cand
                                    break
                        log.debug(
                            "LDAP search matched %d entries; chose dn=%s",
                            len(entry_responses),
                            chosen.get("dn"),
                        )

                    attrs = chosen.get("attributes") or {}
                    _dn = (
                        attrs.get("distinguishedName", [chosen.get("dn")])[0]
                        if isinstance(attrs.get("distinguishedName"), list)
                        else attrs.get("distinguishedName", chosen.get("dn"))
                    )
                    _sam = (attrs.get("sAMAccountName") or [None])[0]
                    log.debug("LDAP user resolved (%s): %s sam=%s", label, _dn, _sam)
                    groups = attrs.get("memberOf") or []
                    log.debug("LDAP memberOf count=%d for %s", len(groups), _dn)
                    for g in groups[:50]:
                        log.debug("  %s", g)

                    # Return a lightweight shim compatible with later code
                    return _entry_shim_from_attrs(attrs, chosen.get("dn"))
                log.debug(
                    "LDAP search 0 results (%s) base=%r filter=%r ldap=%s (%s)",
                    label,
                    search_base,
                    fltr,
                    result.get("result"),
                    result.get("description"),
                )
            except LDAPException as exc:
                log.warning(
                    "LDAP search error (%s) base=%r: %s: %s",
                    label,
                    search_base,
                    type(exc).__name__,
                    exc,
                )

    # Optional discovery mode: try a very small paged ANR search to reveal the
    # directory's naming conventions (useful when UPN prefix != sAMAccountName).
    if os.getenv("LDAP_DEBUG_DISCOVERY", "").strip() == "1" and search_bases:
        _debug_discover_user(
            conn=conn,
            search_base=search_bases[-1],  # domain root is typically last
            sam_account=sam_account,
            upn_variants=upn_variants,
            label=label,
        )
    return None


def _debug_discover_user(
    conn: Connection,
    search_base: str,
    sam_account: str,
    upn_variants: list[str],
    label: str,
) -> None:
    """Best-effort discovery search; never raises."""
    token = sam_account.split(".")[0] if sam_account else sam_account
    token = (token or "").strip()
    if not token:
        return
    fltr = f"(anr={escape_filter_chars(token)})"
    try:
        # Use paged search to avoid size-limit issues; cap output tightly.
        rows = []
        for entry in conn.extend.standard.paged_search(
            search_base=search_base,
            search_filter=fltr,
            attributes=["sAMAccountName", "userPrincipalName", "distinguishedName", "displayName", "mail"],
            paged_size=5,
            generator=True,
        ):
            if entry.get("type") != "searchResEntry":
                continue
            attrs = entry.get("attributes", {}) or {}
            rows.append({
                "sam": attrs.get("sAMAccountName"),
                "upn": attrs.get("userPrincipalName"),
                "mail": attrs.get("mail"),
                "dn": attrs.get("distinguishedName"),
                "displayName": attrs.get("displayName"),
            })
            if len(rows) >= 5:
                break
        if rows:
            samples = "; ".join(
                f"sam={r.get('sam')!r} upn={r.get('upn')!r} dn={r.get('dn')!r}"
                for r in rows
            )
            log.info(
                "LDAP_DEBUG_DISCOVERY (%s): %d row(s) %r @ %r — %s",
                label,
                len(rows),
                fltr,
                search_base,
                samples,
            )
        else:
            log.info("LDAP_DEBUG_DISCOVERY (%s): no rows %r @ %r", label, fltr, search_base)
    except Exception as exc:
        log.warning("LDAP_DEBUG_DISCOVERY (%s) failed: %s: %s", label, type(exc).__name__, exc)


def _entry_shim_from_attrs(attrs: dict, dn: str | None):
    """Create a minimal object with attribute access used elsewhere in this module."""
    class _Attr:
        def __init__(self, value):
            self.value = value
        @property
        def values(self):
            if self.value is None:
                return []
            return self.value if isinstance(self.value, list) else [self.value]

    class _Entry:
        pass

    e = _Entry()
    e.distinguishedName = dn or (attrs.get("distinguishedName", [None])[0] if isinstance(attrs.get("distinguishedName"), list) else attrs.get("distinguishedName"))
    e.sAMAccountName = (attrs.get("sAMAccountName") or [None])[0]
    e.userPrincipalName = _Attr((attrs.get("userPrincipalName") or [None])[0])
    e.mail = _Attr((attrs.get("mail") or [None])[0])
    e.memberOf = _Attr(attrs.get("memberOf") or [])
    return e


def _find_user_search_filters(sam_account: str, upn_variants: list[str]) -> list[str]:
    """LDAP filters to locate a user; UPN for bind may not equal sAMAccountName in AD."""
    safe_sam = escape_filter_chars(sam_account)
    parts: list[str] = [
        f"(sAMAccountName={safe_sam})",
        f"(&(objectCategory=person)(objectClass=user)(sAMAccountName={safe_sam}))",
        f"(cn={safe_sam})",
        f"(anr={safe_sam})",
    ]
    for upn in upn_variants:
        eu = escape_filter_chars(upn)
        parts.append(f"(userPrincipalName={eu})")
        parts.append(f"(&(objectCategory=person)(objectClass=user)(userPrincipalName={eu}))")
        parts.append(f"(mail={eu})")
        parts.append(f"(proxyAddresses=smtp:{eu})")
        parts.append(f"(proxyAddresses=SMTP:{eu})")
    if upn_variants:
        parts.append(f"(anr={escape_filter_chars(upn_variants[0])})")
    return list(dict.fromkeys(parts))


def _membership_check(
    conn: Connection,
    domain_root: str,
    user_entry: object,
    group_dn: str,
    user_dn: str | None = None,
) -> bool:
    """Verify group membership using the AD transitive-membership OID.

    Falls back to a direct string comparison against the memberOf list if the
    recursive LDAP search fails (e.g. the DC doesn't support the OID).
    """
    # Try the recursive LDAP filter first (handles nested groups)
    safe_group_dn = group_dn.replace("(", "\\28").replace(")", "\\29")
    if user_dn:
        safe_user_dn = user_dn.replace("(", "\\28").replace(")", "\\29")
        search_filter = (
            f"(&(objectClass=user)(distinguishedName={safe_user_dn})"
            f"({_MEMBER_OF_RECURSIVE}={safe_group_dn}))"
        )
    else:
        sam = str(user_entry.sAMAccountName)
        search_filter = (
            f"(&(objectClass=user)(sAMAccountName={sam})"
            f"({_MEMBER_OF_RECURSIVE}={safe_group_dn}))"
        )
    try:
        ok, result, response = _search(
            conn,
            search_base=domain_root,
            search_filter=search_filter,
            attributes=[],
        )
        n = len(_entries_from_response(response))
        log.debug("LDAP recursive group match count=%d", n)
        if n > 0:
            return True
    except LDAPException as exc:
        log.debug("LDAP recursive group search failed: %s — using memberOf on user object", exc)

    # Direct memberOf comparison (works for direct membership only)
    groups = getattr(user_entry, "memberOf", None)
    if groups and hasattr(groups, "values"):
        groups = groups.values
    groups = groups or []
    match = any(g.lower() == group_dn.lower() for g in groups)
    log.debug("LDAP direct memberOf match=%s", match)
    return match


def _safe_who_am_i(conn: Connection) -> str | None:
    try:
        who = conn.extend.standard.who_am_i()
        log.debug("LDAP who_am_i: %r", who)
        return who
    except Exception as exc:
        log.debug("who_am_i failed: %s: %s", type(exc).__name__, exc)
        return None


def _read_user_by_dn(conn: Connection, user_dn: str) -> object | None:
    """Read the user object by DN (BASE scope)."""
    try:
        ok, result, response = _search(
            conn,
            search_base=user_dn,
            search_filter="(objectClass=*)",
            search_scope=BASE,
            attributes=["memberOf", "distinguishedName", "sAMAccountName"],
        )
        entries = _entries_from_response(response)
        if not entries:
            log.debug("LDAP BASE read: no entry for DN %r", user_dn)
            return None
        chosen = entries[0]
        attrs = chosen.get("attributes") or {}
        groups = attrs.get("memberOf") or []
        log.debug(
            "LDAP BASE read OK: %s sam=%s memberOf_count=%d",
            chosen.get("dn"),
            (attrs.get("sAMAccountName") or [None])[0],
            len(groups),
        )
        for g in groups[:50]:
            log.debug("  %s", g)
        return _entry_shim_from_attrs(attrs, chosen.get("dn"))
    except LDAPException as exc:
        log.debug("BASE read failed for DN %r: %s", user_dn, exc)
        return None


def _naming_info_from_ldap3_server(server: Server) -> dict:
    """Use DSA info ldap3 loads after bind when Server was created with get_info=DSA."""
    result: dict = {"defaultNamingContext": None, "namingContexts": []}
    info = server.info
    if not info:
        return result
    ncs = info.naming_contexts
    if ncs is None:
        pass
    elif isinstance(ncs, (list, tuple)):
        result["namingContexts"] = [str(x) for x in ncs]
    else:
        result["namingContexts"] = [str(ncs)]
    other = getattr(info, "other", None) or {}
    for k, val in other.items():
        if str(k).lower() != "defaultnamingcontext":
            continue
        if isinstance(val, (list, tuple)) and val:
            result["defaultNamingContext"] = str(val[0])
        elif val is not None:
            result["defaultNamingContext"] = str(val)
        break
    return result


def _read_root_dse_anonymous(ldap_host: str, port: int, use_ssl: bool) -> dict:
    """Read RootDSE without binding (often works when authenticated RootDSE is empty)."""
    result: dict = {"defaultNamingContext": None, "namingContexts": []}
    connect_t, receive_t = _ldap_socket_timeouts()
    try:
        srv = Server(
            ldap_host,
            port=port,
            use_ssl=use_ssl,
            get_info=None,
            connect_timeout=connect_t,
        )
        conn = Connection(
            srv,
            authentication=ANONYMOUS,
            client_strategy=SAFE_SYNC,
            auto_bind=AUTO_BIND_NO_TLS,
            raise_exceptions=True,
            auto_referrals=False,
            receive_timeout=receive_t,
        )
        data = _read_root_dse(conn)
        conn.unbind()
        return data
    except LDAPException as exc:
        log.debug("Anonymous RootDSE not available: %s: %s", type(exc).__name__, exc)
        return result


def _read_root_dse(conn: Connection) -> dict:
    """Read RootDSE naming info — match ldap3 Server._get_dsa_info attribute set."""
    result: dict = {"defaultNamingContext": None, "namingContexts": []}
    try:
        ok, r, response = _search(
            conn,
            search_base="",
            search_filter="(objectClass=*)",
            search_scope=BASE,
            attributes=[
                "altServer",
                "namingContexts",
                "supportedControl",
                "supportedExtension",
                "supportedFeatures",
                "supportedCapabilities",
                "supportedLDAPVersion",
                "supportedSASLMechanisms",
                "vendorName",
                "vendorVersion",
                "subschemaSubentry",
                "*",
                "+",
            ],
            get_operational_attributes=True,
        )
        entries = _entries_from_response(response)
        if entries:
            attrs = entries[0].get("attributes") or {}
            dnc = attrs.get("defaultNamingContext")
            if dnc:
                result["defaultNamingContext"] = dnc[0] if isinstance(dnc, (list, tuple)) else dnc
            ncs = attrs.get("namingContexts")
            if ncs:
                result["namingContexts"] = list(ncs) if not isinstance(ncs, str) else [ncs]
            return result

        # Fallback: parse raw searchResEntry (some AD / ldap3 combos return success with empty .entries)
        if response:
            for resp in response:
                if resp.get("type") != "searchResEntry":
                    continue
                attrs = resp.get("attributes", {}) or {}
                dnc = attrs.get("defaultNamingContext")
                if dnc:
                    result["defaultNamingContext"] = dnc[0] if isinstance(dnc, (list, tuple)) else dnc
                ncs = attrs.get("namingContexts")
                if ncs:
                    result["namingContexts"] = list(ncs) if not isinstance(ncs, str) else [ncs]
                break
        if not result["defaultNamingContext"] and not result["namingContexts"]:
            log.debug(
                "RootDSE returned no naming attributes | ldap=%s (%s) responses=%s",
                r.get("result"),
                r.get("description"),
                len(response) if response else 0,
            )
        return result
    except LDAPException as exc:
        log.debug("RootDSE read failed: %s: %s", type(exc).__name__, exc)
        return result
