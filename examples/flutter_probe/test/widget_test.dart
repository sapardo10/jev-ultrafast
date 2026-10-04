import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jev_probe/main.dart';

void main() {
  testWidgets('hamburger opens the drawer and Settings opens', (tester) async {
    await tester.pumpWidget(const ProbeApp());
    expect(find.text('Home'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.menu));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Settings opened'), findsWidgets);
  });
}
