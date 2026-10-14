import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'./tests/static',timeout:45000,reporter:'list',workers:1,
  use:{baseURL:'http://localhost:4194',screenshot:'only-on-failure',trace:'retain-on-failure'},
  webServer:[
    {command:'node tests/fixtures/static-server.mjs',url:'http://127.0.0.1:4195/manifest.json',reuseExistingServer:true},
    {command:'npm run start -- --hostname localhost --port 4194',url:'http://localhost:4194/sign-in',reuseExistingServer:true,
      env:{AUTH_SECRET:'test-only-secret-not-for-production-123456789',AUTH_URL:'http://localhost:4194',AUTH_TRUST_HOST:'true',
        FPL_PUBLIC_DATA_URL:'http://127.0.0.1:4195',FPL_API_BASE_URL:'http://127.0.0.1:4196',FPL_DASHBOARD_PASSWORD:'test-dashboard-password'}}
  ]});
