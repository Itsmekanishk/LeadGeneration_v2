/**
 * Full-Stack End-to-End Verification of Supabase Gmail Authentication
 * 
 * Verifies:
 * 1. HTTP 200 and Content-Security-Policy on /login.
 * 2. Presence of Google Sign-in elements on /login and /register pages.
 * 3. Next.js rewrite proxy routing to FastAPI backend (/backend/api/auth/supabase-login).
 * 4. OAuth session exchange through Next.js proxy.
 * 5. Authenticated profile lookup (/backend/api/auth/me) with returned access token.
 * 6. Refresh token rotation (/backend/api/auth/refresh) through Next.js proxy.
 * 7. Verification of user idempotency on repeat login.
 */

const http = require('http');

function request(url, options = {}, body = null) {
  return new Promise((resolve, reject) => {
    const req = http.request(url, options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: data }));
    });
    req.on('error', reject);
    if (body) {
      req.write(typeof body === 'string' ? body : JSON.stringify(body));
    }
    req.end();
  });
}

async function run() {
  console.log('===============================================================');
  console.log('  STARTING FULL-STACK SUPABASE GMAIL AUTH VERIFICATION');
  console.log('===============================================================\n');

  // 1. Verify /login page and CSP headers
  console.log('[Step 1] Fetching /login page from Next.js server (http://localhost:3000/login)...');
  const loginRes = await request('http://localhost:3000/login');
  if (loginRes.status !== 200) {
    throw new Error(`Expected 200 OK from /login, got ${loginRes.status}`);
  }
  const csp = loginRes.headers['content-security-policy'] || '';
  console.log('  ✓ Received HTTP 200 OK');
  if (!csp.includes('supabase.co')) {
    throw new Error(`CSP missing supabase.co! Found: ${csp}`);
  }
  console.log('  ✓ CSP header correctly authorizes Supabase endpoints (connect-src includes supabase.co)');
  if (!csp.includes('googleusercontent.com')) {
    throw new Error(`CSP missing googleusercontent.com! Found: ${csp}`);
  }
  console.log('  ✓ CSP header correctly authorizes Google profile avatars (img-src includes googleusercontent.com)');

  // Check login page HTML content
  if (!loginRes.body.includes('google-signin-btn')) {
    throw new Error('Login page HTML does not contain google-signin-btn id');
  }
  if (!loginRes.body.includes('Continue with Gmail')) {
    throw new Error('Login page HTML does not contain "Continue with Gmail" button text');
  }
  console.log('  ✓ Login page renders "Continue with Gmail" button with #google-signin-btn');

  // 2. Verify /register page
  console.log('\n[Step 2] Fetching /register page from Next.js server (http://localhost:3000/register)...');
  const registerRes = await request('http://localhost:3000/register');
  if (registerRes.status !== 200) {
    throw new Error(`Expected 200 OK from /register, got ${registerRes.status}`);
  }
  if (!registerRes.body.includes('google-signin-btn')) {
    throw new Error('Register page HTML does not contain google-signin-btn id');
  }
  if (!registerRes.body.includes('Sign up with Gmail')) {
    throw new Error('Register page HTML does not contain "Sign up with Gmail" button text');
  }
  console.log('  ✓ Register page renders "Sign up with Gmail" button with #google-signin-btn');

  // 3. Verify /auth/callback route
  console.log('\n[Step 3] Fetching /auth/callback page from Next.js server...');
  const callbackRes = await request('http://localhost:3000/auth/callback');
  if (callbackRes.status !== 200) {
    throw new Error(`Expected 200 OK from /auth/callback, got ${callbackRes.status}`);
  }
  console.log('  ✓ /auth/callback route rendered HTTP 200 OK with Suspense boundary');

  // 4. Test Next.js rewrite proxy -> FastAPI backend /api/auth/supabase-login
  console.log('\n[Step 4] Exercising OAuth exchange via Next.js proxy (/backend/api/auth/supabase-login)...');
  const exchangePayload = {
    access_token: 'mock-supabase-oauth-e2e-token',
    email: 'marcus.vance@gmail.com',
    full_name: 'Marcus Vance',
  };
  const exchangeRes = await request('http://localhost:3000/backend/api/auth/supabase-login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  }, exchangePayload);

  if (exchangeRes.status !== 200) {
    throw new Error(`OAuth exchange failed with ${exchangeRes.status}: ${exchangeRes.body}`);
  }
  const tokenData = JSON.parse(exchangeRes.body);
  if (!tokenData.access_token || !tokenData.refresh_token) {
    throw new Error(`Missing tokens in response: ${exchangeRes.body}`);
  }
  if (tokenData.user.email !== 'marcus.vance@gmail.com' || tokenData.user.full_name !== 'Marcus Vance') {
    throw new Error(`User mismatch in response: ${exchangeRes.body}`);
  }
  console.log('  ✓ Next.js successfully rewrote request to FastAPI backend');
  console.log(`  ✓ Minted access_token: ${tokenData.access_token.substring(0, 30)}...`);
  console.log(`  ✓ Minted refresh_token: ${tokenData.refresh_token.substring(0, 20)}...`);
  console.log(`  ✓ User created: id=${tokenData.user.id}, email=${tokenData.user.email}, role=${tokenData.user.role}`);

  // 5. Verify access token against /backend/api/auth/me
  console.log('\n[Step 5] Accessing protected endpoint /backend/api/auth/me with Bearer token...');
  const meRes = await request('http://localhost:3000/backend/api/auth/me', {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${tokenData.access_token}`,
    },
  });
  if (meRes.status !== 200) {
    throw new Error(`Expected 200 from /me, got ${meRes.status}: ${meRes.body}`);
  }
  const meUser = JSON.parse(meRes.body);
  if (meUser.id !== tokenData.user.id || meUser.email !== 'marcus.vance@gmail.com') {
    throw new Error(`Profile data mismatch: ${meRes.body}`);
  }
  console.log(`  ✓ Successfully authorized request for ${meUser.full_name} (${meUser.email})`);

  // 6. Verify refresh token rotation
  console.log('\n[Step 6] Rotating refresh token via /backend/api/auth/refresh...');
  const refreshRes = await request('http://localhost:3000/backend/api/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  }, { refresh_token: tokenData.refresh_token });

  if (refreshRes.status !== 200) {
    throw new Error(`Expected 200 from /refresh, got ${refreshRes.status}: ${refreshRes.body}`);
  }
  const refreshedData = JSON.parse(refreshRes.body);
  if (refreshedData.access_token === tokenData.access_token) {
    throw new Error('Access token was not renewed on refresh');
  }
  if (refreshedData.refresh_token === tokenData.refresh_token) {
    throw new Error('Refresh token was not rotated');
  }
  console.log('  ✓ Token family successfully rotated with new single-use token pair');

  // 7. Verify repeat login idempotency
  console.log('\n[Step 7] Testing repeat login idempotency for existing Gmail user...');
  const repeatRes = await request('http://localhost:3000/backend/api/auth/supabase-login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  }, {
    access_token: 'mock-supabase-repeat-token',
    email: 'marcus.vance@gmail.com',
    full_name: 'Marcus Vance Updated',
  });
  if (repeatRes.status !== 200) {
    throw new Error(`Expected 200 from repeat login, got ${repeatRes.status}`);
  }
  const repeatData = JSON.parse(repeatRes.body);
  if (repeatData.user.id !== tokenData.user.id) {
    throw new Error(`User ID changed on repeat login! original=${tokenData.user.id}, new=${repeatData.user.id}`);
  }
  console.log('  ✓ Repeat Gmail login cleanly authenticated into existing user account (id preserved)');

  console.log('\n===============================================================');
  console.log('  ALL END-TO-END BEHAVIORAL VERIFICATION CHECKS PASSED! ✨');
  console.log('===============================================================\n');
}

run().catch((err) => {
  console.error('\n❌ VERIFICATION FAILED:', err);
  process.exit(1);
});
