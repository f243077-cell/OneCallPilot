// Copies the contract fixture timelines into assets/fixtures/timelines/ for
// mock mode (MOB-017). The copies are git-ignored: contracts/ stays the only
// source, and nothing from it is committed under mobile/.
//
// Run from mobile/:
//   dart run tool/sync_fixtures.dart                     # from ../contracts
//   dart run tool/sync_fixtures.dart --from <contracts>  # another checkout
//   OCP_CONTRACTS_DIR=<contracts> dart run tool/sync_fixtures.dart
//
// Until the C1 fixtures are merged into main, point it at a worktree of the
// branch that has them, for example:
//   git worktree add ../../ocp-usman origin/usman
//   dart run tool/sync_fixtures.dart --from ../../ocp-usman/contracts

import 'dart:io';

const target = 'assets/fixtures/timelines';

void main(List<String> args) {
  final index = args.indexOf('--from');
  final contracts = index >= 0 && index + 1 < args.length
      ? args[index + 1]
      : Platform.environment['OCP_CONTRACTS_DIR'] ?? '../contracts';
  final source = Directory('$contracts/fixtures/timelines');
  final files = source.existsSync()
      ? (source
            .listSync()
            .whereType<File>()
            .where((f) => f.path.endsWith('.json'))
            .toList()
          ..sort((a, b) => a.path.compareTo(b.path)))
      : <File>[];
  if (files.isEmpty) {
    stderr.writeln('No timelines in ${source.path}; nothing copied.');
    exit(1);
  }
  final out = Directory(target)..createSync(recursive: true);
  for (final stale in out.listSync().whereType<File>()) {
    if (stale.path.endsWith('.json')) stale.deleteSync();
  }
  for (final file in files) {
    file.copySync('${out.path}/${file.uri.pathSegments.last}');
  }
  stdout.writeln('Copied ${files.length} timelines to $target/');
}
