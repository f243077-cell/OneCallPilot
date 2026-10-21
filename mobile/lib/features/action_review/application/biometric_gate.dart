// Local confirmation for `approval_requirement = biometric` (MOB-009, C8 step 2,
// architecture §10.5): `local_auth` with `biometricOnly: true`. With no
// biometric enrolled the approval is blocked; there is never a fallback to PIN,
// pattern or tap.

import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';

enum BiometricOutcome { confirmed, cancelled, notEnrolled, unavailable }

abstract interface class BiometricGate {
  Future<BiometricOutcome> confirm(String reason);
}

class LocalAuthGate implements BiometricGate {
  LocalAuthGate([LocalAuthentication? auth])
    : _auth = auth ?? LocalAuthentication();

  final LocalAuthentication _auth;

  @override
  Future<BiometricOutcome> confirm(String reason) async {
    try {
      if ((await _auth.getAvailableBiometrics()).isEmpty) {
        return BiometricOutcome.notEnrolled;
      }
      final ok = await _auth.authenticate(
        localizedReason: reason,
        biometricOnly: true,
        persistAcrossBackgrounding: true,
      );
      return ok ? BiometricOutcome.confirmed : BiometricOutcome.cancelled;
    } on LocalAuthException catch (e) {
      return switch (e.code) {
        LocalAuthExceptionCode.noBiometricsEnrolled ||
        LocalAuthExceptionCode.noCredentialsSet => BiometricOutcome.notEnrolled,
        LocalAuthExceptionCode.userCanceled ||
        LocalAuthExceptionCode.systemCanceled ||
        LocalAuthExceptionCode.timeout => BiometricOutcome.cancelled,
        _ => BiometricOutcome.unavailable,
      };
    } on PlatformException {
      return BiometricOutcome.unavailable;
    }
  }
}

final biometricGateProvider = Provider<BiometricGate>((ref) => LocalAuthGate());
