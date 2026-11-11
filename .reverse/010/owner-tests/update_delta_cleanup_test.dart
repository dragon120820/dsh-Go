// Proposed owner-side companion test for 5309.
// Destination: app/test/update/update_delta_cleanup_test.dart.
// Not applied to that directory: it is outside this task's writable test scope.
// Flutter/Dart execution has not been performed in the author environment.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dsh_app/state/update_state.dart';
import 'package:dsh_app/update/installer.dart';
import 'package:dsh_app/update/update_config.dart';
import 'package:dsh_app/update/update_http.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'support.dart';

const _version = '1.2.0+11011200';
const _apk = 'dsh-go-1.2.0-arm64.apk';
const _patchName = 'dsh-go-1.2.0-from-1.0.0-arm64.dshdelta';
const _feed = 'https://dl.dshtui.com/update.json';
const _dl = 'https://dl.dshtui.com/download/1.2.0';

final _old = utf8.encode('PK the installed 1.0.0, ${'resources ' * 300}${'classes ' * 300}');
final _new = [..._old.sublist(0, 2000), ...utf8.encode('the 1.2.0 code'), ..._old.sublist(2400)];
final _patch = deltaPatch(_old, _new, [
  deltaCopy(0, 2000),
  deltaData(utf8.encode('the 1.2.0 code')),
  deltaCopy(2400, _old.length - 2400),
  deltaEnd,
]);

String _sha(List<int> bytes) => sha256.convert(bytes).toString();

Map<String, Object?> _manifest({bool patches = true}) => {
  'schema': 1,
  'version': _version,
  'tag': 'v1.2.0',
  'assets': {
    'arm64': {'name': _apk, 'url': '$_dl/$_apk', 'size': _new.length, 'sha256': _sha(_new)},
  },
  if (patches)
    'patches': {
      'arm64': [
        {'from': _sha(_old), 'name': _patchName, 'size': _patch.length, 'sha256': _sha(_patch)},
      ],
    },
};

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late Directory dir;
  late File installed;
  late FakeHttp http;
  late FakeInstaller installer;

  Directory downloads() => Directory('${dir.path}/updates');
  File built() => File('${downloads().path}/$_apk');
  List<String> names() => downloads().listSync().map((file) => file.uri.pathSegments.last).toList();

  setUp(() {
    dir = Directory.systemTemp.createTempSync('dsh_update_delta_cleanup');
    installed = File('${dir.path}/base.apk')..writeAsBytesSync(_old);
    http = FakeHttp({_feed: (_, _) => textResponse(jsonEncode(_manifest()))});
    installer = FakeInstaller(
      app: InstalledApp(
        versionName: '1.0.0',
        versionCode: 1,
        abi: 'arm64',
        freeBytes: 8 << 30,
        apkPath: installed.path,
        lastUpdateTime: 1000,
      ),
    );
    SharedPreferences.setMockInitialValues({});
  });
  tearDown(() => dir.deleteSync(recursive: true));

  Future<(ProviderContainer, AppUpdateController)> checked() async {
    final container = ProviderContainer(
      overrides: [
        updateHttpProvider.overrideWithValue(http),
        updateInstallerProvider.overrideWithValue(installer),
        updateSourcesProvider.overrideWithValue(buildUpdateSources()),
        updateClockProvider.overrideWithValue(() => DateTime(2026, 11, 2, 9)),
        updateFlavorNameProvider.overrideWithValue('arm64'),
        updateDirectoryProvider.overrideWithValue(() async => downloads()),
      ],
    );
    addTearDown(container.dispose);
    final controller = container.read(appUpdateProvider.notifier);
    await controller.ready;
    expect(await controller.check(), UpdateCheckPhase.available);
    return (container, controller);
  }

  test('a failed partial patch is gone before the full APK fallback starts', () async {
    var patchRequests = 0;
    final dropping = fileRoute(_patch, dropAfter: 40);
    http.routes['$_dl/$_patchName'] = (url, headers) {
      if (patchRequests++ == 0) return dropping(url, headers);
      throw const SocketException('connection unavailable after the drop');
    };
    List<String>? filesAtFallback;
    final full = fileRoute(_new);
    http.routes['$_dl/$_apk'] = (url, headers) {
      filesAtFallback = names();
      return full(url, headers);
    };

    final (container, controller) = await checked();
    await controller.startDownload();

    expect(container.read(appUpdateProvider).task, UpdateTaskPhase.ready);
    expect(container.read(appUpdateProvider).patchFailedVersion, _version);
    expect(patchRequests, 2, reason: 'the partial prefix was retried before falling back');
    expect(filesAtFallback, isNotNull);
    expect(filesAtFallback, isNot(contains('$_patchName.part')));
    expect(filesAtFallback, isNot(contains(_patchName)));
    expect(names(), [_apk]);
    expect(built().readAsBytesSync(), _new);
    expect(installed.readAsBytesSync(), _old);
    expect(installer.installed, isEmpty, reason: 'this regression does not install an APK');
  });

  test('cancelling a partial patch preserves it and the next download resumes it', () async {
    final (container, controller) = await checked();
    final listening = Completer<void>();
    final body = StreamController<List<int>>(sync: true, onListen: () => listening.complete());
    http.routes['$_dl/$_patchName'] = (_, _) =>
        UpdateHttpResponse(status: 200, contentLength: _patch.length, body: body.stream);
    final downloading = controller.startDownload();
    try {
      await listening.future;
      body.add(_patch.sublist(0, 40));
      controller.cancelDownload();
      await downloading;

      expect(container.read(appUpdateProvider).task, UpdateTaskPhase.idle);
      expect(container.read(appUpdateProvider).patchFailedVersion, isNull);
      expect(File('${downloads().path}/$_patchName.part').readAsBytesSync(), _patch.sublist(0, 40));

      int? resumedAt;
      http.routes['$_dl/$_patchName'] = fileRoute(_patch, onRequest: (start) => resumedAt = start);
      await controller.startDownload();

      expect(resumedAt, 40);
      expect(container.read(appUpdateProvider).task, UpdateTaskPhase.ready);
      expect(container.read(appUpdateProvider).patchFailedVersion, isNull);
      expect(names(), [_apk]);
      expect(built().readAsBytesSync(), _new);
      expect(http.urls.where((url) => url.endsWith('/$_apk')), isEmpty);
    } finally {
      controller.cancelDownload();
      await body.close();
      await downloading;
    }
  });

  test('a failed full APK keeps its partial prefix for the normal retry', () async {
    http.routes[_feed] = (_, _) => textResponse(jsonEncode(_manifest(patches: false)));
    var requests = 0;
    final dropping = fileRoute(_new, dropAfter: 40);
    http.routes['$_dl/$_apk'] = (url, headers) {
      if (requests++ == 0) return dropping(url, headers);
      throw const SocketException('connection unavailable after the drop');
    };
    final (container, controller) = await checked();
    await controller.startDownload();

    expect(container.read(appUpdateProvider).task, UpdateTaskPhase.failed);
    expect(container.read(appUpdateProvider).problem, UpdateProblem.network);
    expect(container.read(appUpdateProvider).patchFailedVersion, isNull);
    expect(File('${downloads().path}/$_apk.part').readAsBytesSync(), _new.sublist(0, 40));

    int? resumedAt;
    http.routes['$_dl/$_apk'] = fileRoute(_new, onRequest: (start) => resumedAt = start);
    await controller.retry();

    expect(resumedAt, 40);
    expect(container.read(appUpdateProvider).task, UpdateTaskPhase.ready);
    expect(names(), [_apk]);
    expect(built().readAsBytesSync(), _new);
    expect(installer.installed, isEmpty);
  });
}
