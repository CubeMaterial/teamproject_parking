import 'package:flutter_test/flutter_test.dart';

import 'package:yeouido_parking_flutter/main.dart';

void main() {
  testWidgets('shows admin login page', (WidgetTester tester) async {
    await tester.pumpWidget(const MyApp());

    expect(find.text('관리자 로그인'), findsOneWidget);
    expect(find.text('관리자 계정으로 로그인해 주세요'), findsOneWidget);
    expect(find.text('회원가입'), findsNothing);
  });
}
