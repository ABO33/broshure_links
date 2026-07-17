import { Tabs } from 'expo-router';
import { Text, type ColorValue } from 'react-native';

import { colors, typography } from '@/theme';

const tabIcon = (symbol: string, color: ColorValue) => (
  <Text style={{ color, fontSize: typography.sizes.lg }}>{symbol}</Text>
);

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          minHeight: 64,
          paddingTop: 6,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{ title: 'Home', tabBarIcon: ({ color }) => tabIcon('⌂', color) }}
      />
      <Tabs.Screen
        name="search"
        options={{ title: 'Search', tabBarIcon: ({ color }) => tabIcon('⌕', color) }}
      />
      <Tabs.Screen
        name="library"
        options={{ title: 'My Library', tabBarIcon: ({ color }) => tabIcon('▦', color) }}
      />
      <Tabs.Screen
        name="profile"
        options={{ title: 'Profile', tabBarIcon: ({ color }) => tabIcon('●', color) }}
      />
    </Tabs>
  );
}
