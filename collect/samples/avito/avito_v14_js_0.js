function getCookie(name) {
        var match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return match ? decodeURIComponent(match[1]) : null;
    }

    function b64DecodeUnicode(str) {
        str = str.replace(/-/g, '+').replace(/_/g, '/');
        while (str.length % 4) str += '=';
        return decodeURIComponent(atob(str).split('').map(function(c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
    }

    function parseJwtPayload(jwt) {
        var parts = jwt.split('.');
        if (parts.length < 2) throw new Error('invalid jwt');
        return JSON.parse(b64DecodeUnicode(parts[1]));
    }

    async function sha256hex(str) {
        var buf = new TextEncoder().encode(str);
        var hash = await crypto.subtle.digest('SHA-256', buf);
        var arr = new Uint8Array(hash);
        var hex = '';
        for (var i = 0; i < arr.length; i++) {
            hex += ('0' + arr[i].toString(16)).slice(-2);
        }
        return hex;
    }

    async function solvePow(id, complexity) {
        var prefix = '';
        for (var i = 0; i < complexity; i++) prefix += '0';
        var nonce = 0;
        while (true) {
            var hex = await sha256hex(id + ':' + nonce);
            if (hex.substring(0, complexity) === prefix) {
                return nonce;
            }
            nonce++;
            if (nonce % 10000 === 0) {
                await new Promise(function(r) { setTimeout(r, 0); });
            }
        }
    }

    var MAX_RETRIES = 3;

    function showFinalError(msg) {
        document.getElementById('pow-spinner').style.display = 'none';
        document.getElementById('pow-status-text').textContent = msg;
    }

    function showStatus(msg) {
        document.getElementById('pow-spinner').style.display = 'inline-block';
        document.getElementById('pow-status-text').textContent = msg;
        document.getElementById('pow-error').style.display = 'none';
    }

    async function fetchWithTimeout(url, options, timeoutMs) {
        var controller = new AbortController();
        var timeoutId = setTimeout(function() {
            controller.abort();
        }, timeoutMs);

        try {
            return await fetch(url, Object.assign({}, options, {
                signal: controller.signal,
            }));
        } finally {
            clearTimeout(timeoutId);
        }
    }

    async function startPow(attempt) {
        attempt = attempt || 1;
        try {
            showStatus('Получение задания...');

            var challenge = getCookie('pow_challenge');
            if (!challenge) {
                showFinalError('Не удалось получить данные для проверки. Обновите страницу.');
                return;
            }

            var getResp = await fetchWithTimeout('/web/3/firewallPow/get', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ challenge: challenge }),
            }, 15000);
            var getData = await getResp.json();

            if (!getData || !getData.success || !getData.success.result || !getData.success.result.challenge_jwt) {
                throw new Error('no challenge_jwt');
            }

            var challengeJwt = getData.success.result.challenge_jwt;
            var payload = parseJwtPayload(challengeJwt);
            var id = payload.id;
            var complexity = payload.compl;

            showStatus('Один момент...');

            var nonce = await solvePow(id, complexity);

            showStatus('Отправка результата...');

            var verifyResp = await fetchWithTimeout('/web/3/firewallPow/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ challenge: challengeJwt, nonce: nonce }),
            }, 15000);
            var verifyData = await verifyResp.json();

            if (verifyData && verifyData.success && verifyData.success.result && verifyData.success.result.verified) {
                document.cookie = 'pow_solved=1; Path=/; Max-Age=10; SameSite=Lax';
                showStatus('Проверка пройдена, перенаправление...');
                setTimeout(function() { window.location = location.href; }, 300);
                return;
            } else {
                throw new Error('not verified');
            }
        } catch (err) {
            console.error(err);
            if (attempt < MAX_RETRIES) {
                setTimeout(function() { startPow(attempt + 1); }, 1000);
            } else {
                showFinalError('Не удалось пройти проверку. Обновите страницу.');
            }
        }
    }

    document.addEventListener('DOMContentLoaded', function() {
        if (getCookie('pow_solved')) {
            showStatus('Проверка пройдена, перенаправление...');
            setTimeout(function() { window.location = location.href; }, 3000);
            return;
        }
        startPow(1);
    });