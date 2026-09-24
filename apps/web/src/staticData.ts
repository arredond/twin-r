// Static data files the frontend reads directly, not through the scenario
// API: the three PMTiles archives (buildings/debris/municipalities) and
// faults.json (ADR-0022). All live under `tiles/` in the public data bucket
// (ADR-0016). Deployed builds set just VITE_S3_DATA_BUCKET (the stack's
// `DataBucketName` output); unset, local dev serves them from
// apps/web/public/data instead. The region is hardcoded rather than a
// second env var: it must match the stack's region (infra/, deployed to
// eu-south-2), and eu-south-2 is an opt-in region -- S3's region-less
// `<bucket>.s3.amazonaws.com` endpoint 400s for it, so the URL can't just
// leave it out.
const S3_DATA_BUCKET_REGION = "eu-south-2";

export function staticDataUrl(filename: string): string {
  const bucket = import.meta.env.VITE_S3_DATA_BUCKET;
  return bucket
    ? `https://${bucket}.s3.${S3_DATA_BUCKET_REGION}.amazonaws.com/tiles/${filename}`
    : `/data/${filename}`;
}
