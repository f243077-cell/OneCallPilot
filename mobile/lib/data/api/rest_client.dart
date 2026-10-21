// REST transport for the live repository (architecture §10.1): dio with the
// Supabase access token in the `Authorization` header (never in the URL,
// SEC-011), explicit timeouts, one session refresh and retry on 401, and every
// failure mapped in one place: an answer from the server becomes an ApiError
// with its C2 code; no answer (no connection, a timeout) becomes NetworkError.

import 'package:dio/dio.dart';

import '../../core/errors/api_error.dart';
import 'ws_client.dart';

class RestClient {
  RestClient(String baseUrl, this._tokens, {Dio? dio})
    : _dio =
          dio ??
          Dio(
            BaseOptions(
              connectTimeout: const Duration(seconds: 10),
              sendTimeout: const Duration(seconds: 10),
              receiveTimeout: const Duration(seconds: 15),
            ),
          ) {
    _dio.options
      ..baseUrl = baseUrl
      ..responseType = ResponseType.json
      ..validateStatus = (status) => status != null && status < 300;
  }

  final Dio _dio;
  final TokenSource _tokens;

  Dio get dio => _dio;

  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, dynamic>? query,
  }) => _send(
    (headers) => _dio.get<Object?>(
      path,
      queryParameters: query,
      options: Options(headers: headers),
    ),
  );

  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body, {
    String? idempotencyKey,
  }) => _send(
    (headers) => _dio.post<Object?>(
      path,
      data: body,
      options: Options(
        headers: {...headers, 'Idempotency-Key': ?idempotencyKey},
      ),
    ),
  );

  Future<Map<String, dynamic>> _send(
    Future<Response<Object?>> Function(Map<String, String> headers) request,
  ) async {
    for (var attempt = 0; ; attempt++) {
      final token = await _tokens.accessToken();
      if (token == null) {
        throw const ApiError(
          ApiErrorCode.unauthorized,
          status: 401,
          message: 'Not signed in',
        );
      }
      try {
        final response = await request({'Authorization': 'Bearer $token'});
        final data = response.data;
        if (data is Map<String, dynamic>) return data;
        throw ApiError(
          ApiErrorCode.internal,
          status: response.statusCode ?? 500,
          message: 'Unexpected response body',
        );
      } on DioException catch (e) {
        final error = mapDioError(e);
        if (error is ApiError &&
            error.code == ApiErrorCode.unauthorized &&
            attempt == 0 &&
            await _tokens.refresh()) {
          continue; // once, with the refreshed token
        }
        throw error;
      }
    }
  }
}

/// A server answer → ApiError (from its envelope when it has one); no answer
/// → NetworkError.
Exception mapDioError(DioException e) {
  final response = e.response;
  if (response == null) {
    return NetworkError(switch (e.type) {
      DioExceptionType.connectionTimeout ||
      DioExceptionType.sendTimeout ||
      DioExceptionType.receiveTimeout => 'Timed out',
      _ => 'No connection',
    });
  }
  final status = response.statusCode ?? 500;
  final data = response.data;
  if (data is Map<String, dynamic> && data['error'] is Map<String, dynamic>) {
    try {
      return ApiError.fromEnvelope(status, data);
    } on TypeError {
      // fall through: an envelope that does not match C2
    }
  }
  return ApiError(
    status == 401 ? ApiErrorCode.unauthorized : ApiErrorCode.internal,
    status: status,
    message: 'HTTP $status',
  );
}
