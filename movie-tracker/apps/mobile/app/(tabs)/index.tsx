import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { EmptyState } from '@/components/EmptyState';
import { Screen } from '@/components/Screen';
import { colors, spacing, typography } from '@/theme';

const sections = ['Trending', 'Popular', 'Upcoming', 'Top rated'];

export default function HomeScreen() {
  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <View style={styles.hero}>
          <Text style={styles.eyebrow}>YOUR NEXT GREAT FILM</Text>
          <Text accessibilityRole="header" style={styles.title}>
            Find it. Save it. Remember it.
          </Text>
          <Text style={styles.subtitle}>
            Movie discovery arrives in Phase 3. The navigation and data foundation are ready.
          </Text>
        </View>
        {sections.map((section) => (
          <View key={section} style={styles.section}>
            <Text accessibilityRole="header" style={styles.sectionTitle}>
              {section}
            </Text>
            <EmptyState
              compact
              message="Movie cards will appear here when discovery is connected."
            />
          </View>
        ))}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.lg, gap: spacing.xl },
  hero: {
    backgroundColor: colors.surfaceElevated,
    borderColor: colors.border,
    borderRadius: 24,
    borderWidth: 1,
    gap: spacing.md,
    padding: spacing.xl,
  },
  eyebrow: { color: colors.accent, fontSize: typography.sizes.xs, fontWeight: '800' },
  title: { color: colors.text, fontSize: typography.sizes.display, fontWeight: '800' },
  subtitle: { color: colors.textMuted, fontSize: typography.sizes.md, lineHeight: 24 },
  section: { gap: spacing.md },
  sectionTitle: { color: colors.text, fontSize: typography.sizes.xl, fontWeight: '700' },
});
