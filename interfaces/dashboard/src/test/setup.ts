/**
 * Test setup — unmount rendered components between tests.
 *
 * Testing Library normally installs this itself, but only by registering
 * on the GLOBAL ``afterEach``, which doesn't exist here: the runner is
 * configured ``globals: false`` (see vitest.config.ts) so tests import
 * their own ``describe``/``it``.  With no global hook to hook, RTL's
 * auto-cleanup silently never installs.
 *
 * What that costs is false PASSES, not failures.  Every render's nodes
 * stay in the document, so the next test's queries can find the previous
 * test's output and its assertions pass without the component under test
 * doing anything.  Found the hard way: a set of guard tests all passed
 * while reading a leftover DOM, and only showed their real result once
 * cleanup was added by hand.
 *
 * Registering it here means a test file can't forget — and the per-file
 * ``afterEach(cleanup)`` calls that already exist stay correct, since
 * cleanup is idempotent.
 */
import { afterEach } from 'vitest';
import { cleanup, configure } from '@testing-library/react';

afterEach(cleanup);

/**
 * Testing Library keeps its OWN async budget, and vitest.config.ts
 * cannot raise it: ``waitFor`` / ``findBy*`` give up after 1000ms
 * regardless of ``testTimeout``.  On a contended machine a React
 * render plus its effects can miss that comfortably, and the test
 * then fails with "Unable to find element" — which reads like a
 * broken selector rather than a starved scheduler, so it sends you
 * hunting through the component.
 *
 * 5s here matches the intent of the raised vitest timeouts: long
 * enough that only a genuinely-never-appearing element fails, short
 * enough that a real failure doesn't hold the suite for 20s.
 */
configure({ asyncUtilTimeout: 5_000 });
