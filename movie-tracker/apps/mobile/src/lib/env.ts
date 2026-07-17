import { z } from 'zod';

const publicEnvironmentSchema = z.object({
  apiBaseUrl: z.url(),
  supabaseUrl: z.union([z.url(), z.literal('')]),
  supabaseAnonKey: z.string(),
});

export const publicEnvironment = publicEnvironmentSchema.parse({
  apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL ?? 'http://localhost:8000',
  supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL ?? '',
  supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? '',
});
