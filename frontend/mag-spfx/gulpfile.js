'use strict';

const build = require('@microsoft/sp-build-web');

build.addSuppression('Warning - [sass] The local CSS class is not camelCase and will not be type-safe.');

build.configureWebpack.mergeConfig({
  additionalConfiguration: (generatedConfiguration) => {
    const webpack = require('webpack');
    const fs = require('fs');
    const path = require('path');
    const envPath = path.resolve(__dirname, '../app/.env.local');
    const env = {};

    if (fs.existsSync(envPath)) {
      console.log(`[gulpfile.js] Reading environment variables from ${envPath}`);
      const content = fs.readFileSync(envPath, 'utf8');
      content.split(/\r?\n/).forEach((line) => {
        const trimmedLine = line.trim();
        if (!trimmedLine || trimmedLine.startsWith('#')) {
          return;
        }
        const match = trimmedLine.match(/^\s*([\w.-]+)\s*=\s*(.*)?\s*$/);
        if (match) {
          const key = match[1];
          let value = match[2] || '';
          if (value.startsWith('"') && value.endsWith('"')) {
            value = value.slice(1, -1);
          } else if (value.startsWith("'") && value.endsWith("'")) {
            value = value.slice(1, -1);
          }
          env[key] = value.trim();
        }
      });
    } else {
      console.warn(`[gulpfile.js] Environment file not found at ${envPath}`);
    }

    const publicClientEnvironmentKeys = new Set([
      'VITE_USE_LOCAL_DOCKER',
      'VITE_AZURE_API_TARGET',
      'VITE_API_TARGET',
      'VITE_USE_MOCK_DATA',
      'VITE_POLL_INTERVAL_MS',
      'MAG_API_BASE_URL',
      'MAG_ENTRA_TENANT_ID',
      'MAG_ENTRA_CLIENT_ID',
      'MAG_ENTRA_BACKEND_SCOPE',
      'MAG_ENTRA_REDIRECT_URI',
      'MAG_ENTRA_AUTHORITY'
    ]);
    const definitions = {};
    for (const key of publicClientEnvironmentKeys) {
      const value = process.env[key] || env[key] || '';
      definitions[`process.env.${key}`] = JSON.stringify(value);
      if (value) console.log(`[gulpfile.js] Defining public client environment variable ${key}`);
    }

    generatedConfiguration.plugins.push(new webpack.DefinePlugin(definitions));
    return generatedConfiguration;
  }
});

build.initialize(require('gulp'));
