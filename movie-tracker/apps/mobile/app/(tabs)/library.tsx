import { EmptyState } from '@/components/EmptyState';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';

export default function LibraryScreen() {
  return (
    <Screen padded>
      <ScreenHeader title="My Library" subtitle="Watchlist and watched titles arrive in Phase 5." />
      <EmptyState message="Your saved movies will live here." />
    </Screen>
  );
}
