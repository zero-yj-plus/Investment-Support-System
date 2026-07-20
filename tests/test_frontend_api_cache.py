from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
API_URI = (ROOT / "frontend" / "js" / "api.js").resolve().as_uri()
NODE = shutil.which("node")


def test_get_requests_are_single_flight_cached_and_explicitly_invalidated() -> None:
    if NODE is None:
        pytest.skip("node is required for the frontend cache contract")
    script = f"""
      import assert from 'node:assert/strict';
      let calls = 0;
      globalThis.fetch = async () => {{
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 5));
        return {{ ok: true, status: 200, statusText: 'OK', json: async () => ({{ calls }}) }};
      }};
      const {{ api }} = await import('{API_URI}');
      const [a, b] = await Promise.all([api.get('/pulse'), api.get('/pulse')]);
      assert.equal(calls, 1);
      assert.deepEqual(a, b);
      await api.get('/pulse');
      assert.equal(calls, 1);
      api.invalidate('/pulse');
      await api.get('/pulse');
      assert.equal(calls, 2);
      await api.get('/pulse', {{ force: true }});
      assert.equal(calls, 3);
    """
    result = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_timed_out_get_releases_singleflight_and_can_retry() -> None:
    if NODE is None:
        pytest.skip("node is required for the frontend timeout contract")
    script = f"""
      import assert from 'node:assert/strict';
      let calls = 0;
      globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {{
        calls += 1;
        options.signal.addEventListener('abort', () => {{
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        }}, {{ once: true }});
      }});
      const {{ api }} = await import('{API_URI}');
      await assert.rejects(api.get('/never', {{ timeoutMs: 15 }}), /数据源响应超过/);
      await assert.rejects(api.get('/never', {{ timeoutMs: 15 }}), /数据源响应超过/);
      assert.equal(calls, 2, 'timeout must release the old inflight request');
    """
    result = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_internal_timeout_still_applies_with_caller_signal() -> None:
    if NODE is None:
        pytest.skip("node is required for the combined abort contract")
    script = f"""
      import assert from 'node:assert/strict';
      globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {{
        options.signal.addEventListener('abort', () => {{
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        }}, {{ once: true }});
      }});
      const {{ api }} = await import('{API_URI}');
      const external = new AbortController();
      await assert.rejects(
        api.get('/caller-signal-timeout', {{ signal: external.signal, timeoutMs: 15 }}),
        /数据源响应超过/
      );

      const cancelled = new AbortController();
      const pending = api.get('/caller-cancel', {{ signal: cancelled.signal, timeoutMs: 5000 }});
      cancelled.abort();
      await assert.rejects(pending, (error) => error.name === 'AbortError');
    """
    result = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_stale_cache_returns_immediately_and_refreshes_in_background() -> None:
    if NODE is None:
        pytest.skip("node is required for the frontend SWR contract")
    script = f"""
      import assert from 'node:assert/strict';
      let calls = 0;
      let release = null;
      globalThis.fetch = async () => {{
        calls += 1;
        const call = calls;
        if (call === 2) await new Promise((resolve) => {{ release = resolve; }});
        return {{ ok: true, status: 200, statusText: 'OK', json: async () => ({{ call }}) }};
      }};
      const {{ api }} = await import('{API_URI}');
      const first = await api.get('/swr', {{ ttlMs: 1, staleTtlMs: 60_000 }});
      assert.equal(first.call, 1);
      await new Promise((resolve) => setTimeout(resolve, 5));

      const stale = await api.get('/swr', {{ ttlMs: 1, staleTtlMs: 60_000 }});
      assert.equal(stale.call, 1, 'stale last-good must render without waiting');
      assert.equal(calls, 2, 'stale read must start one background refresh');

      const staleAgain = await api.get('/swr', {{ ttlMs: 1, staleTtlMs: 60_000 }});
      assert.equal(staleAgain.call, 1);
      assert.equal(calls, 2, 'background refresh remains single-flight');
      release();
      await new Promise((resolve) => setTimeout(resolve, 0));

      const fresh = await api.get('/swr', {{ ttlMs: 60_000, staleTtlMs: 60_000 }});
      assert.equal(fresh.call, 2, 'background result replaces last-good cache');
    """
    result = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
