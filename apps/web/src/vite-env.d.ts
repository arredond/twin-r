/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SCENARIO_API_URL?: string;
  readonly VITE_S3_DATA_BUCKET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
