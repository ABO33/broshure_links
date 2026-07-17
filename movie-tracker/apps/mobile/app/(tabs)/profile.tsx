import { EmptyState } from '@/components/EmptyState';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';

export default function ProfileScreen() {
  return (
    <Screen padded>
      <ScreenHeader
        title="Profile"
        subtitle="Authentication and profile setup arrive in Phase 2."
      />
      <EmptyState message="Sign-in controls and your statistics will appear here." />
    </Screen>
  );
}
