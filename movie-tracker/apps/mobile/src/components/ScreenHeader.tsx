import { StyleSheet, Text, View } from 'react-native';

import { colors, spacing, typography } from '@/theme';

type ScreenHeaderProps = { title: string; subtitle: string };

export function ScreenHeader({ title, subtitle }: ScreenHeaderProps) {
  return (
    <View style={styles.container}>
      <Text accessibilityRole="header" style={styles.title}>
        {title}
      </Text>
      <Text style={styles.subtitle}>{subtitle}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: spacing.sm },
  title: { color: colors.text, fontSize: typography.sizes.display, fontWeight: '800' },
  subtitle: { color: colors.textMuted, fontSize: typography.sizes.md, lineHeight: 24 },
});
