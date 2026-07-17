import { StyleSheet, Text, View } from 'react-native';

import { colors, spacing, typography } from '@/theme';

type EmptyStateProps = { message: string; compact?: boolean };

export function EmptyState({ message, compact = false }: EmptyStateProps) {
  return (
    <View accessibilityLabel={message} style={[styles.container, compact && styles.compact]}>
      <View style={styles.mark} />
      <Text style={styles.message}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    borderColor: colors.border,
    borderRadius: 20,
    borderStyle: 'dashed',
    borderWidth: 1,
    gap: spacing.md,
    justifyContent: 'center',
    minHeight: 220,
    padding: spacing.xl,
  },
  compact: { minHeight: 150 },
  mark: { backgroundColor: colors.accentMuted, borderRadius: 20, height: 40, width: 40 },
  message: {
    color: colors.textMuted,
    fontSize: typography.sizes.sm,
    lineHeight: 20,
    textAlign: 'center',
  },
});
