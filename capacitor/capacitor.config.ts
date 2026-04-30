import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'nl.fieldopsapp.app',
  appName: 'FieldOps',
  webDir: 'www',
  // Wijs Capacitor naar de live productie portaal — geen lokale assets nodig
  server: {
    url: 'https://fieldopsapp.nl/portaal',
    cleartext: false,
    androidScheme: 'https',
    iosScheme: 'https',
    allowNavigation: [
      'fieldopsapp.nl',
      '*.fieldopsapp.nl',
      'fieldops-api-8txr.onrender.com',
    ],
  },
  ios: {
    contentInset: 'always',
    backgroundColor: '#0a0f1eff',
    limitsNavigationsToAppBoundDomains: false,
    scrollEnabled: true,
  },
  android: {
    backgroundColor: '#0a0f1eff',
    allowMixedContent: false,
    captureInput: true,
    webContentsDebuggingEnabled: false,
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 1500,
      launchAutoHide: true,
      backgroundColor: '#0a0f1e',
      androidSplashResourceName: 'splash',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
      splashImmersive: true,
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#0a0f1e',
      overlaysWebView: false,
    },
    PushNotifications: {
      presentationOptions: ['badge', 'sound', 'alert'],
    },
    Geolocation: {
      // GPS-toestemming string in Info.plist (iOS) wordt apart in Xcode gezet
    },
    Camera: {
      // Camera-toestemming string idem
    },
  },
};

export default config;
