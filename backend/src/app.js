console.log('START APPLICATION');
const express = require('express');
const path = require('path');
const favicon = require('serve-favicon');
const logger = require('morgan');
const cookieParser = require('cookie-parser');
const bodyParser = require('body-parser');
const swaggerJSDoc = require('swagger-jsdoc');
const swaggerUi = require('swagger-ui-express');

const index = require('./routes/index');
const internal = require('./routes/internal');
const gates = require('./routes/gates');
const tickets = require('./routes/tickets');
const sse = require('./routes/sse');

const app = express();

// uncomment after placing your favicon in /public
//app.use(favicon(path.join(__dirname, 'public', 'favicon.ico')));
app.use(logger('dev'));
app.use(bodyParser.json());
app.use(bodyParser.urlencoded({extended: false}));
app.use(cookieParser());
app.use(express.static(path.join(__dirname, '..', '..', 'frontend/build')));

app.use('/api/gates', gates);
app.use('/api/tickets', tickets);
app.use('/stream', sse.router);

const swaggerApi = express.Router();
// https://github.com/swagger-api/swagger-spec/blob/master/versions/2.0.md
const swaggerDefinition = {
    info: {
        title: 'Gatekeeper',
        version: '0.0.1',
        description: 'Managing Gates'
    },
    // host: 'localhost:3000',
    basePath: '/api',
};

const swaggerSpec = swaggerJSDoc({
    swaggerDefinition: swaggerDefinition,
    apis: ['./src/routes/*.js'],
});

swaggerApi.get('/api-docs.json', function (req, res) {
    res.json(swaggerSpec);
});

swaggerApi.use('/', swaggerUi.serve, swaggerUi.setup(swaggerSpec));

app.use('/api', swaggerApi);
app.use('/internal', internal);
app.use('*', index);

// catch 404 and forward to error handler
app.use(function (req, res, next) {
    const err = new Error('Not Found');
    err.status = 404;
    next(err);
});

// error handler
app.use(function (err, req, res, next) {
    // set locals, only providing error in development
    res.locals.message = err.message;
    res.locals.error = req.app.get('env') === 'development' ? err : {};

    // render the error page
    res.status(err.status || 500);
    res.json(err);
});

module.exports = app;
