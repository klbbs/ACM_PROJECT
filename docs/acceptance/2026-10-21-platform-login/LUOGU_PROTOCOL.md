# Luogu official login protocol, observed 2026-10-21

Scope: read-only official JS analysis plus empty-storage browser navigation. No real login, password, private cookies, CAPTCHA solving, or submission. Repository docs already recorded the browser/session-vs-requests418 limitation but no current password protocol.

## Runtime evidence

- `ev_f97f1cc9c7f90952cb01be996b0ca5ac1c8792c73bd04641333a65817fd60b5a`: REA launched owned Chromium 156.0.8078.4 with empty cookies/local/session storage at https://www.luogu.com.cn/auth/login; only action was 1000ms wait. Main navigation yielded 302 then 200. Rendered login initially asks username/UID/phone/email, next step. Lentille JSON root keys instance/template/status/locale/data/user/time; user=null, instance=auth, template=login, status=200. Network request/response body/header values disabled. Provider process terminated. No CDP endpoint used.
- Authenticated official in-app browser DOM (read-only, no storage reads) has the same Lentille envelope; root user.uid matches current navigation identity. Public profile payload lives under data.user and remains distinct.
- `ev_850aaab925721ad25a18bcbcc23d4d84cf3c178657ebee2f7cf9349136543929`: empty-storage navigation to publicly discovered https://www.luogu.com.cn/_lfe/config/auth?_version=01c79f47db1e9b46 returned HTTP200 application/json. Body bytes SHA256 b84fd3f78c19cb57f638163be2dcf984001546ac4a8b3d39178fb021494eda8b. Official route names map auth.login_methods=/auth/login-methods, do_auth.password=/do-auth/password, captcha=/lg4/captcha, user.setting=/user/setting.
- `ev_a7ba78f0ce752ada0acaee24c8fb2be1e140032f99664fd05088d1ae885cea3d`: empty-storage read-only GET /lg4/captcha gives 302 then 200. No image bytes or CAPTCHA answer read.
- Anonymous Python requests GET /auth/login and / both returned HTTP418, 67-byte bodies, no scripts. A normal requests Session is not demonstrated usable despite empty-storage Chromium navigation working.

## Current official static protocol

Downloaded only official script URLs observed by browser DOM/network, saved at /tmp/acm-luogu-auth-js. No scripts executed during static analysis.

1. GET /auth/login and retain the same client/session. Header X-CSRF-TOKEN takes meta[name=csrf-token].content. Shared Axios also adds X-Requested-With: XMLHttpRequest. No claim that these two headers suffice to avoid access restrictions.
2. GET /auth/login-methods?login=<input>. Response JSON root default, available, fido2 determines authenticator. UI treats 11-digit numeric input as phone with default +86 prefix unless user chooses not-phone.
3. Password authenticator auto-loads img URL /lg4/captcha?_t=<Date.now()+Math.random()>. Captcha helper fz only resolves the route URL; img performs GET. Clicking image refreshes it and clears answer.
4. POST /do-auth/password with object {username:<account input>, password:<literal password input>, captcha:<typed captcha or empty string>}. Axios posts object as JSON. Component makes no password hash/transformation.
5. Client destructures response JSON root redirectTo, and navigates when set. Failure calls passwordAuthenticator.getNewCaptcha(), refreshing captcha and clearing answer. Final authenticated identity must come from official session envelope, not redirect alone or public profile.
6. Root lentille-context.user is session identity in current vendor module 37655: currentUser computed from registry.user, while currentData is registry.data. Existing old explicit currentUser at root/data can remain supported, but conflicting representations must fail closed. Never use data.user.

Main source locations (zero-based JS character offsets, decoded UTF-8):
- columba~54648a8bf7ae5854.js around 2400-3100: login-methods GET, do_auth.<method> POST, redirectTo handling, failure refresh.
- columba~c5cad6ac2dcaff8c.js around 1200-1700: submit password literal and login captcha; around 2750: auto CAPTCHA component.
- columba~9ab891a215e2a944.js around 650-900: captcha route resolution/cache-buster/answer reset; around 1400: img src.
- lauth.20261018-2305.js around 183200: meta CSRF -> X-CSRF-TOKEN.
- vendor.20261018-2305.js module 37655, relative offsets 9765-10700: root user/currentUser vs data/currentData. Relative offsets 13846 onward route resolver; around relative 15000 get/post wrappers. Module 29808 around absolute 624000 shared Axios header and requests.

Hashes:
- lauth.20261018-2305.js edeaf4a7eb13859e9283717a104d8f8f0e703fe1ea5d6cf6042609a3f4901f09
- vendor.20261018-2305.js ad27fdfd4698ee2b561b2f2f2cfbd5b62f7f9747d9d1d705b447b61613d44da9
- columba~54648a8bf7ae5854.js 4945617695061bc8bbf8cd8a72900266664d700608127290f058c5baa66fe2e1
- columba~c5cad6ac2dcaff8c.js a0937c55e6ee5e2d108053e6282435f34fc1855b888f0af36b9652d4f66d7d86
- columba~9ab891a215e2a944.js d8e85f95b0a8e5c6746adecea99c666c3aa91769ffec84120de6a89edfbb61a0

All script URLs use https://fecdn.luogu.com.cn/columba/<filename> and were observed in the runtime evidence above.

## REA static evidence and limits

- Auth/source graph parent `ev_8892b41bc3a265ecbee38732dc0d9e1af525b49e2ec65d71e81dec4d4e46e474`, summary `ev_f7e745c351ef9218fa8965f3fe18149258ff10b973833e2e7d72778a0f9e335c`: selected five auth sources, all parsed; unselected vendor/font/libs outside this pass. Semantic graph partial due static/dynamic unknowns and node ceiling. Script byte identity and source text retained; no execution claimed.
- Vendor-only graph parent `ev_7ef8e51c67f8bc8a1363d4e5bd7d5a94a1adf2fa614b71e90ec0fc59b95f4ad8`, summary `ev_2f834e499102905f0547e5a436e5b301062932f51ad101eae6f1a5bb30a8d2ce`: parsed shared vendor source; graph/semantic coverage partial due other files and node ceiling.
- Initial oldfe preliminary parent `ev_1d03fd02a11ad81c7060a7ef5adacf99254e204a0afd6cb95883e509ab703a9d` only established shared CSRF and old scripts; current auth facts rely on newer auth sources above.

Unknowns: server-side CAPTCHA session binding/TTL, whether requests can work at another exit, actual bad/valid login responses, multi-factor continuation responses, exact success redirect and cookie changes. Same-session requirement is conservative inference from same-origin browser/Axios/image use and absence of a separate captcha token; not a successful cross-session experiment. Do not claim real login success.

## Local identity parser correction

Changed only src/acmweb/credential_check.py and tests/test_credential_independent_review.py. Root user.uid now accepted as current identity; legacy currentUser remains supported; contradicting null/UID, invalid UID, duplicate JSON fields/scripts, inert/template/external scripts fail unknown. Active challenge still overrides identity-like markup; data.user never authenticates.

Verification: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q tests/test_credential_check.py tests/test_credential_independent_review.py => 118 passed. git diff --check passed. Default pytest plugin autoload failed before collection because unrelated ROS launch_testing plugin imports missing yaml; no dependency changes made.
