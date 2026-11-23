<?php
declare(strict_types=1);

/**
 * AI Usage Dashboard Relay (Xserver PHP)
 * スマホ版 (GitHub Pages HTTPS) <-> Xserver PHP <-> PC (api_server.py)
 */

header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate');

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

$EXPECTED_TOKEN = getenv('AI_USAGE_RELAY_TOKEN') ?: 'aiu_relay_2026_k9m4x7p2';
$token = (string)($_GET['token'] ?? '');
if (!hash_equals($EXPECTED_TOKEN, $token)) {
    http_response_code(403);
    echo json_encode(['ok' => false, 'error' => 'Unauthorized'], JSON_UNESCAPED_UNICODE);
    exit;
}

$action = (string)($_GET['action'] ?? '');
$stateFile = sys_get_temp_dir() . '/ai_usage_relay_state_' . substr(sha1($EXPECTED_TOKEN), 0, 12) . '.json';

function with_state(string $path, callable $fn) {
    $fp = fopen($path, 'c+');
    if ($fp === false) {
        return ['ok' => false, 'error' => 'State file open failed', '_status' => 500];
    }
    try {
        if (!flock($fp, LOCK_EX)) {
            return ['ok' => false, 'error' => 'Lock failed', '_status' => 500];
        }
        $raw = stream_get_contents($fp);
        $state = $raw ? json_decode($raw, true) : null;
        if (!is_array($state)) {
            $state = ['pc_last_seen' => 0, 'jobs' => []];
        }
        if (!isset($state['jobs']) || !is_array($state['jobs'])) {
            $state['jobs'] = [];
        }
        $now = time();
        // 300秒以上前の古いジョブを削除
        foreach ($state['jobs'] as $jid => $j) {
            if (!is_array($j) || ($now - (int)($j['created_at'] ?? 0)) > 300) {
                unset($state['jobs'][$jid]);
            }
        }
        $res = $fn($state, $now);
        ftruncate($fp, 0);
        rewind($fp);
        fwrite($fp, json_encode($state, JSON_UNESCAPED_UNICODE));
        fflush($fp);
        flock($fp, LOCK_UN);
        return $res;
    } finally {
        fclose($fp);
    }
}

$allowedTargets = ['all', 'claude', 'codex', 'antigravity'];

if ($action === 'request') {
    $target = strtolower(trim((string)($_GET['target'] ?? 'all')));
    if (!in_array($target, $allowedTargets, true)) {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Invalid target'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $res = with_state($stateFile, function (&$state, $now) use ($target) {
        foreach ($state['jobs'] as $jid => $j) {
            $st = $j['status'] ?? '';
            if (($st === 'pending' || $st === 'running') && ($now - (int)($j['created_at'] ?? 0)) < 180) {
                if (($j['target'] ?? '') === $target || ($j['target'] ?? '') === 'all') {
                    return [
                        'ok' => true,
                        'job_id' => $jid,
                        'status' => $st,
                        'target' => $j['target'],
                        'reused' => true,
                        'pc_online' => ($now - (int)($state['pc_last_seen'] ?? 0)) <= 20,
                    ];
                }
            }
        }
        $jid = bin2hex(random_bytes(8));
        $state['jobs'][$jid] = [
            'id' => $jid,
            'target' => $target,
            'status' => 'pending',
            'created_at' => $now,
            'updated_at' => $now,
        ];
        return [
            'ok' => true,
            'job_id' => $jid,
            'status' => 'pending',
            'target' => $target,
            'reused' => false,
            'pc_online' => ($now - (int)($state['pc_last_seen'] ?? 0)) <= 20,
        ];
    });
    http_response_code($res['_status'] ?? 200);
    unset($res['_status']);
    echo json_encode($res, JSON_UNESCAPED_UNICODE);
    exit;
}

if ($action === 'poll') {
    $res = with_state($stateFile, function (&$state, $now) {
        $state['pc_last_seen'] = $now;
        foreach ($state['jobs'] as $jid => &$j) {
            if (($j['status'] ?? '') === 'pending' && ($now - (int)($j['created_at'] ?? 0)) <= 180) {
                $j['status'] = 'running';
                $j['updated_at'] = $now;
                return ['ok' => true, 'job' => ['id' => $jid, 'target' => $j['target']]];
            }
        }
        return ['ok' => true, 'job' => null];
    });
    http_response_code($res['_status'] ?? 200);
    unset($res['_status']);
    echo json_encode($res, JSON_UNESCAPED_UNICODE);
    exit;
}

if ($action === 'complete') {
    $bodyRaw = file_get_contents('php://input');
    $body = $bodyRaw ? json_decode($bodyRaw, true) : null;
    if (!is_array($body) || empty($body['id'])) {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Invalid payload'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $res = with_state($stateFile, function (&$state, $now) use ($body) {
        $state['pc_last_seen'] = $now;
        $jid = (string)$body['id'];
        if (!isset($state['jobs'][$jid])) {
            return ['ok' => false, 'error' => 'Job not found', '_status' => 404];
        }
        $state['jobs'][$jid]['status'] = !empty($body['ok']) ? 'done' : 'error';
        $state['jobs'][$jid]['updated_at'] = $now;
        $state['jobs'][$jid]['service_error'] = $body['service_error'] ?? null;
        $state['jobs'][$jid]['error'] = $body['error'] ?? null;
        $state['jobs'][$jid]['data'] = $body['data'] ?? null;
        return ['ok' => true];
    });
    http_response_code($res['_status'] ?? 200);
    unset($res['_status']);
    echo json_encode($res, JSON_UNESCAPED_UNICODE);
    exit;
}

if ($action === 'status') {
    $jid = (string)($_GET['id'] ?? '');
    $res = with_state($stateFile, function (&$state, $now) use ($jid) {
        $pcOnline = ($now - (int)($state['pc_last_seen'] ?? 0)) <= 20;
        if ($jid === '' || !isset($state['jobs'][$jid])) {
            return ['ok' => false, 'error' => 'Job not found', 'pc_online' => $pcOnline, '_status' => 404];
        }
        $j = $state['jobs'][$jid];
        return [
            'ok' => true,
            'id' => $jid,
            'target' => $j['target'] ?? 'all',
            'status' => $j['status'] ?? 'pending',
            'service_error' => $j['service_error'] ?? null,
            'error' => $j['error'] ?? null,
            'data' => $j['data'] ?? null,
            'pc_online' => $pcOnline,
            'age_sec' => $now - (int)($j['created_at'] ?? $now),
        ];
    });
    http_response_code($res['_status'] ?? 200);
    unset($res['_status']);
    echo json_encode($res, JSON_UNESCAPED_UNICODE);
    exit;
}

http_response_code(400);
echo json_encode(['ok' => false, 'error' => 'Unknown action'], JSON_UNESCAPED_UNICODE);
