import { EmptyState } from '@/components/EmptyState';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';

export default function SearchScreen() {
  return (
    <Screen padded>
      <ScreenHeader
        title="Search"
        subtitle="Search history and debounced discovery arrive in Phase 4."
      />
      <EmptyState message="Start exploring once movie search is connected." />
    </Screen>
  );
}
